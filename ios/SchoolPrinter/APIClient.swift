import Foundation

@MainActor
final class APIClient: ObservableObject {
    @Published private(set) var isLoading = false
    @Published var lastError: String?

    private let session: URLSession
    private let defaults = UserDefaults.standard

    init() {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 300        // 5 min per request (large uploads)
        cfg.timeoutIntervalForResource = 1800      // 30 min total (huge PDFs)
        cfg.waitsForConnectivity = true
        // keep the connection alive during long uploads
        cfg.httpMaximumConnectionsPerHost = 4
        session = URLSession(configuration: cfg)
    }

    // MARK: - Config

    var baseURL: String {
        get { defaults.string(forKey: AppConfig.userDefaultsBaseURLKey) ?? AppConfig.defaultBaseURL }
        set { defaults.set(newValue, forKey: AppConfig.userDefaultsBaseURLKey) }
    }

    var token: String? {
        get { defaults.string(forKey: AppConfig.userDefaultsTokenKey) }
        set { defaults.set(newValue, forKey: AppConfig.userDefaultsTokenKey) }
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
    }

    // MARK: - Core request

    private func makeRequest(_ path: String, method: String = "GET", body: Data? = nil, contentType: String = "application/json") throws -> URLRequest {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path) else {
            throw APIError.invalidURL
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        if let t = token { req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
        if let body = body { req.httpBody = body }
        return req
    }

    private func run<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        isLoading = true
        defer { isLoading = false }
        let (data, resp): (Data, URLResponse)
        do {
            (data, resp) = try await session.data(for: req)
        } catch {
            throw APIError.network(error)
        }
        guard let http = resp as? HTTPURLResponse else { throw APIError.network(URLError(.badServerResponse)) }
        if http.statusCode == 401 { throw APIError.unauthorized }
        if !(200..<300).contains(http.statusCode) {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.http(status: http.statusCode, body: body)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    // MARK: - Endpoints

    func login(username: String, password: String) async throws -> LoginResponse {
        let body = try JSONEncoder().encode(["username": username, "password": password])
        let req = try makeRequest("/auth/login", method: "POST", body: body)
        let res: LoginResponse = try await run(req, as: LoginResponse.self)
        self.token = res.access_token
        // fetch and store the full User object
        if let me = try? await me() { self.currentUser = me }
        return res
    }

    func me() async throws -> User {
        let req = try makeRequest("/users/me")
        return try await run(req, as: User.self)
    }

    func listJobs(allUsers: Bool = false) async throws -> [Job] {
        let path = "/jobs?limit=50" + (allUsers ? "&include_all=true" : "")
        let req = try makeRequest(path)
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
        let (_, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw APIError.network(URLError(.badServerResponse)) }
        if !(200..<300).contains(http.statusCode) {
            throw APIError.http(status: http.statusCode, body: "")
        }
    }

    func uploadAndPrint(
        fileURL: URL,
        filename: String,
        mimeType: String,
        options: PrintOptions
    ) async throws -> Job {
        let (data, boundary) = try makeMultipartBody(
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
            fileFieldName: "file",
            endpoint: "/print"
        )
        var req = try makeRequest("/print", method: "POST", body: data, contentType: "multipart/form-data; boundary=\(boundary)")
        req.setValue("\(data.count)", forHTTPHeaderField: "Content-Length")
        return try await run(req, as: Job.self)
    }

    /// Upload a PDF to the server to inspect page count (does not print).
    /// Returns PdfInfo including a spool_file token to use for preview rendering.
    func uploadForInfo(
        fileURL: URL, filename: String, mimeType: String
    ) async throws -> PdfInfo {
        let (data, boundary) = try makeMultipartBody(
            fileURL: fileURL, filename: filename, mimeType: mimeType,
            fields: [:], fileFieldName: "file",
            endpoint: "/pdf-info"
        )
        var req = try makeRequest("/pdf-info", method: "POST", body: data, contentType: "multipart/form-data; boundary=\(boundary)")
        req.setValue("\(data.count)", forHTTPHeaderField: "Content-Length")
        return try await run(req, as: PdfInfo.self)
    }

    /// Fetch a JPEG thumbnail of a single page from a previously-uploaded PDF.
    func fetchPreview(spoolFile: String, page: Int, width: Int = 200) async throws -> PagePreview {
        let path = "/preview?spool_file=\(spoolFile)&page=\(page)&width=\(width)"
        let req = try makeRequest(path)
        return try await run(req, as: PagePreview.self)
    }

    // MARK: - Multipart helper

    /// Reads the file at `fileURL`, appends fields + file into a multipart/form-data body.
    /// Returns (body, boundary).
    private func makeMultipartBody(
        fileURL: URL,
        filename: String,
        mimeType: String,
        fields: [String: String],
        fileFieldName: String,
        endpoint: String
    ) throws -> (Data, String) {
        let accessing = fileURL.startAccessingSecurityScopedResource()
        defer { if accessing { fileURL.stopAccessingSecurityScopedResource() } }
        let fileData = try Data(contentsOf: fileURL)
        let boundary = "----SPBoundary\(UUID().uuidString)"
        var body = Data()
        func append(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!)
            body.append("\(value)\r\n".data(using: .utf8)!)
        }
        for (k, v) in fields { append(k, v) }
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"\(fileFieldName)\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(fileData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        return (body, boundary)
    }
}
