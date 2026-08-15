import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case http(status: Int, body: String)
    case decoding(Error)
    case network(Error)
    /// 401 on the login call itself — the credentials were wrong.
    case badCredentials
    /// 401 on any other call — the stored token is expired or revoked.
    /// Distinct from `badCredentials` so an expired session doesn't tell the
    /// user their password is wrong.
    case sessionExpired
    case rateLimited(String)
    case missingToken

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid server URL. Check the address in Settings."
        case .http(let s, let b): return "Server error \(s): \(Self.detail(from: b) ?? "")"
        case .decoding(let e): return "Could not parse response: \(e.localizedDescription)"
        case .network(let e): return "Network error: \(e.localizedDescription)"
        case .badCredentials: return "Invalid username or password."
        case .sessionExpired: return "Your session expired. Please sign in again."
        case .rateLimited(let m): return m
        case .missingToken: return "Not logged in."
        }
    }

    /// FastAPI returns errors as {"detail": "..."} — surface just that.
    static func detail(from body: String) -> String? {
        guard let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return body.isEmpty ? nil : body }
        return obj["detail"] as? String ?? body
    }
}

struct AppConfig {
    /// Default server URL. User can override in Settings.
    /// The Pi's address changes between networks — see ServerSettingsView.
    static let defaultBaseURL = "http://192.168.1.106:8000"
    static let userDefaultsBaseURLKey = "server.baseURL"
    static let userDefaultsUserKey = "auth.userJSON"
    /// Keychain account name for the bearer token. The token is deliberately
    /// NOT in UserDefaults: that is readable from unencrypted device backups,
    /// which matters on shared or MDM-managed school iPads.
    static let keychainTokenAccount = "auth.token"
    static let keychainService = "com.kenny.schoolprinter"

    /// Validate a user-entered server URL. Returns a normalised string, or nil.
    /// Catches the common "192.168.1.106:8000" (no scheme) case, which
    /// otherwise produces a URL whose *scheme* is the IP address and fails
    /// every request with an opaque error.
    static func normalizeBaseURL(_ raw: String) -> String? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.lowercased().hasPrefix("http://") && !s.lowercased().hasPrefix("https://") {
            s = "http://" + s
        }
        while s.hasSuffix("/") { s.removeLast() }
        guard let comps = URLComponents(string: s),
              let scheme = comps.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = comps.host, !host.isEmpty
        else { return nil }
        return s
    }
}

struct User: Codable, Identifiable, Equatable {
    let id: Int
    let username: String
    let display_name: String?
    let is_admin: Bool
    let is_active: Bool
    let created_at: Int
    let last_login: Int?

    enum CodingKeys: String, CodingKey {
        case id, username, display_name, is_admin, is_active, created_at, last_login
    }
}

struct LoginResponse: Codable {
    let access_token: String
    let token_type: String
    let expires_in: Int
    let user_id: Int
    let username: String
    let is_admin: Bool
}

struct PrintOptions: Codable {
    var copies: Int = 1
    var duplex: Bool = false
    var colorMode: String = "monochrome"
    var pageRange: String = ""           // "" = all, "1-3,5", etc.
    var paperSize: String = ""           // "", "A4", "Letter", ...
    var orientation: String = ""         // "", "portrait", "landscape", "auto"
    var scaling: String = ""             // "", "fit", "actual", "shrink", or "85"
    var layoutDirection: String = ""     // "", "ltr", "rtl"
}

struct Job: Codable, Identifiable, Equatable {
    let id: Int
    let user_id: Int
    let username: String?
    let filename: String
    let mime_type: String?
    let size_bytes: Int
    let copies: Int
    let duplex: Bool
    let color_mode: String
    let page_range: String?
    let paper_size: String?
    let orientation: String?
    let scaling: String?
    let layout_direction: String?
    let cups_job_id: Int?
    let status: String
    let error: String?
    // Defaulted so older/newer servers that omit the field still decode.
    let needs_conversion: Bool?
    let created_at: Int
    let printed_at: Int?

    // spool_path is intentionally absent: the server no longer returns it (it
    // leaked filesystem paths) and no view ever used it.
    enum CodingKeys: String, CodingKey {
        case id, user_id, username, filename, mime_type, size_bytes
        case copies, duplex, color_mode
        case page_range, paper_size, orientation, scaling, layout_direction
        case cups_job_id, status, error, needs_conversion, created_at, printed_at
    }

    var createdDate: Date { Date(timeIntervalSince1970: TimeInterval(created_at)) }
    var printedDate: Date? { printed_at.map { Date(timeIntervalSince1970: TimeInterval($0)) } }
}

struct PdfInfo: Codable {
    let pages: Int
    let page_size: String?
    let is_pdf: Bool
    let spool_file: String?
    let needs_conversion: Bool
}

struct PagePreview: Codable {
    let page: Int
    let image_base64: String
    let width: Int
    let height: Int
}
