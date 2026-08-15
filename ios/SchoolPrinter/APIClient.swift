import Foundation

// MARK: - Keychain

/// Minimal Keychain wrapper for the bearer token.
///
/// The token used to live in UserDefaults, which is readable from unencrypted
/// device backups — a real exposure on shared or MDM-managed school iPads.
enum Keychain {
    static func set(_ value: String?, account: String, service: String) {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)
        guard let value, let data = value.data(using: .utf8) else { return }
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(add as CFDictionary, nil)
    }

    static func get(account: String, service: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

@MainActor
final class APIClient: ObservableObject {
    @Published private(set) var isLoading = false
    @Published var lastError: String?
    /// Bumped whenever the session is cleared so RootView re-evaluates.
    @Published private(set) var sessionID = UUID()

    private let session: URLSession
    private let defaults = UserDefaults.standard

    init() {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 300        // 5 min per request (large uploads)
        cfg.timeoutIntervalForResource = 1800      // 30 min total (huge PDFs)
        cfg.waitsForConnectivity = true
        cfg.httpMaximumConnectionsPerHost = 4
        session = URLSession(configuration: cfg)
    }

    // MARK: - Config

    var baseURL: String {
        get { defaults.string(forKey: AppConfig.userDefaultsBaseURLKey) ?? AppConfig.defaultBaseURL }
        set { defaults.set(newValue, forKey: AppConfig.userDefaultsBaseURLKey) }
    }

    var token: String? {
        get { Keychain.get(account: AppConfig.keychainTokenAccount, service: AppConfig.keychainService) }
        set { Keychain.set(newValue, account: AppConfig.keychainTokenAccount, service: AppConfig.keychainService) }
    }

    var currentUser: User? {
        get {
            guard let data = defaults.data(forKey: AppConfig.userDefaultsUserKey) else { return nil }
            return try? JSONDecoder().decode(User.self, from: data)
        }
        set {
            if let v = newValue, let data = try? JSONEncoder().encode(v) {
                defaults.set(data, forKey: AppConfig.userDefaultsUserKey)
            } else {
                defaults.removeObject(forKey: AppConfig.userDefaultsUserKey)
            }
        }
    }

    var isLoggedIn: Bool { token != nil && currentUser != nil }

    func logout() {
        token = nil
        currentUser = nil
        sessionID = UUID()
    }

    // MARK: - Core request

    private func makeRequest(
        _ path: String,
        method: String = "GET",
        query: [URLQueryItem] = [],
        body: Data? = nil,
        contentType: String = "application/json"
    ) throws -> URLRequest {
        // Build via URLComponents so query values are percent-encoded. Spool
        // filenames inherit the user's filename suffix and can contain spaces
        // or non-ASCII, which string interpolation would turn into a nil URL.
        guard var comps = URLComponents(string: baseURL) else { throw APIError.invalidURL }
        comps.path = (comps.path as NSString).appendingPathComponent(path)
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw APIError.invalidURL }

        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        if let t = token { req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
        if let body { req.httpBody = body }
        return req
    }

    /// Shared response handling.
    /// - Parameter isLogin: distinguishes "wrong password" from "session expired",
    ///   and prevents a failed login from clearing an unrelated session.
    private func handle<T: Decodable>(
        data: Data, resp: URLResponse, as type: T.Type, isLogin: Bool
    ) throws -> T {
        guard let http = resp as? HTTPURLResponse else {
            throw APIError.network(URLError(.badServerResponse))
        }
        if http.statusCode == 401 {
            if isLogin { throw APIError.badCredentials }
            // The stored token is no longer usable — drop it so the UI returns
            // to the login screen instead of showing errors on every tab.
            logout()
            throw APIError.sessionExpired
        }
        let bodyText = String(data: data, encoding: .utf8) ?? ""
        if http.statusCode == 429 {
            throw APIError.rateLimited(APIError.detail(from: bodyText) ?? "Too many attempts. Please wait and try again.")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(status: http.statusCode, body: bodyText)
        }
        if T.self == EmptyResponse.self, let empty = EmptyResponse() as? T { return empty }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    private func run<T: Decodable>(_ req: URLRequest, as type: T.Type, isLogin: Bool = false) async throws -> T {
        isLoading = true
        defer { isLoading = false }
        let data: Data, resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch {
            throw APIError.network(error)
        }
        return try handle(data: data, resp: resp, as: type, isLogin: isLogin)
    }

    // MARK: - Endpoints

    @discardableResult
    func login(username: String, password: String) async throws -> LoginResponse {
        let body = try JSONEncoder().encode(["username": username, "password": password])
        let req = try makeRequest("/auth/login", method: "POST", body: body)
        let res: LoginResponse = try await run(req, as: LoginResponse.self, isLogin: true)
        self.token = res.access_token
        // Fetch the full User object; if this fails the session is unusable,
        // so surface the error rather than landing in a half-signed-in state.
        do {
            self.currentUser = try await me()
        } catch {
            logout()
            throw error
        }
        sessionID = UUID()
        return res
    }

    func me() async throws -> User {
        let req = try makeRequest("/users/me")
        return try await run(req, as: User.self)
    }

    /// Validates the stored token on launch. Returns true if the session is live.
    /// A 401 here logs out via `handle`, so the caller just re-renders.
    @discardableResult
    func validateSession() async -> Bool {
        guard isLoggedIn else { return false }
        do {
            self.currentUser = try await me()
            return true
        } catch {
            return false
        }
    }

    func changePassword(current: String, new: String) async throws -> User {
        let body = try JSONEncoder().encode([
            "current_password": current, "new_password": new,
        ])
        let req = try makeRequest("/users/me", method: "PATCH", body: body)
        return try await run(req, as: User.self)
    }

    func listJobs(allUsers: Bool = false) async throws -> [Job] {
        var q = [URLQueryItem(name: "limit", value: "50")]
        if allUsers { q.append(URLQueryItem(name: "include_all", value: "true")) }
        let req = try makeRequest("/jobs", query: q)
        return try await run(req, as: [Job].self)
    }

    func getJob(_ id: Int) async throws -> Job {
        let req = try makeRequest("/jobs/\(id)")
        return try await run(req, as: Job.self)
    }

    func refreshJob(_ id: Int) async throws -> Job {
        let req = try makeRequest("/jobs/\(id)/refresh", method: "POST")
        return try await run(req, as: Job.self)
    }

    func cancelJob(_ id: Int) async throws {
        let req = try makeRequest("/jobs/\(id)", method: "DELETE")
        _ = try await run(req, as: EmptyResponse.self)
    }

    func uploadAndPrint(
        fileURL: URL,
        filename: String,
        mimeType: String,
        options: PrintOptions
    ) async throws -> Job {
        try await upload(
            path: "/print",
            fileURL: fileURL, filename: filename, mimeType: mimeType,
            fields: [
                "copies": String(options.copies),
                "duplex": options.duplex ? "true" : "false",
                "color_mode": options.colorMode,
                "page_range": options.pageRange,
                "paper_size": options.paperSize,
                "orientation": options.orientation,
                "scaling": options.scaling,
                "layout_direction": options.layoutDirection,
            ],
            as: Job.self
        )
    }

    /// Upload a PDF to the server to inspect page count (does not print).
    func uploadForInfo(
        fileURL: URL, filename: String, mimeType: String
    ) async throws -> PdfInfo {
        try await upload(
            path: "/pdf-info",
            fileURL: fileURL, filename: filename, mimeType: mimeType,
            fields: [:], as: PdfInfo.self
        )
    }

    /// Fetch a JPEG thumbnail of a single page from a previously-uploaded PDF.
    func fetchPreview(spoolFile: String, page: Int, width: Int = 200) async throws -> PagePreview {
        let req = try makeRequest("/preview", query: [
            URLQueryItem(name: "spool_file", value: spoolFile),
            URLQueryItem(name: "page", value: String(page)),
            URLQueryItem(name: "width", value: String(width)),
        ])
        return try await run(req, as: PagePreview.self)
    }

    // MARK: - Streaming multipart upload

    /// Builds the multipart body on DISK and uploads with `uploadTask(from:)`.
    ///
    /// The previous implementation read the whole file into memory with
    /// `Data(contentsOf:)` and then appended it into a second `Data`, so a
    /// 50 MB PDF needed ~100 MB resident and large files got the app jetsammed.
    /// Here the source file is copied through in 256 KB chunks and URLSession
    /// streams the result from disk.
    private func upload<T: Decodable>(
        path: String,
        fileURL: URL,
        filename: String,
        mimeType: String,
        fields: [String: String],
        as type: T.Type
    ) async throws -> T {
        let boundary = "----SPBoundary\(UUID().uuidString)"
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("upload-\(UUID().uuidString).multipart")
        defer { try? FileManager.default.removeItem(at: tmp) }

        try writeMultipart(to: tmp, fileURL: fileURL, filename: filename,
                           mimeType: mimeType, fields: fields, boundary: boundary)

        let req = try makeRequest(path, method: "POST",
                                  contentType: "multipart/form-data; boundary=\(boundary)")
        isLoading = true
        defer { isLoading = false }
        let data: Data, resp: URLResponse
        do {
            (data, resp) = try await session.upload(for: req, fromFile: tmp)
        } catch {
            throw APIError.network(error)
        }
        return try handle(data: data, resp: resp, as: type, isLogin: false)
    }

    private func writeMultipart(
        to dest: URL,
        fileURL: URL,
        filename: String,
        mimeType: String,
        fields: [String: String],
        boundary: String
    ) throws {
        let accessing = fileURL.startAccessingSecurityScopedResource()
        defer { if accessing { fileURL.stopAccessingSecurityScopedResource() } }

        FileManager.default.createFile(atPath: dest.path, contents: nil)
        guard let out = FileHandle(forWritingAtPath: dest.path) else {
            throw APIError.network(URLError(.cannotCreateFile))
        }
        defer { try? out.close() }

        func write(_ s: String) throws {
            try out.write(contentsOf: Data(s.utf8))
        }

        for (k, v) in fields {
            try write("--\(boundary)\r\n")
            try write("Content-Disposition: form-data; name=\"\(k)\"\r\n\r\n")
            try write("\(v)\r\n")
        }

        try write("--\(boundary)\r\n")
        try write("Content-Disposition: form-data; name=\"file\"; filename=\"\(Self.escapeQuotes(filename))\"\r\n")
        try write("Content-Type: \(mimeType)\r\n\r\n")

        let input = try FileHandle(forReadingFrom: fileURL)
        defer { try? input.close() }
        while true {
            let chunk = try input.read(upToCount: 256 * 1024) ?? Data()
            if chunk.isEmpty { break }
            try out.write(contentsOf: chunk)
        }

        try write("\r\n--\(boundary)--\r\n")
    }

    /// A quote in a filename would terminate the Content-Disposition value early.
    private static func escapeQuotes(_ s: String) -> String {
        s.replacingOccurrences(of: "\"", with: "'")
    }
}

/// Placeholder for endpoints that return 204 No Content.
struct EmptyResponse: Decodable {}
