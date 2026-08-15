import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case http(status: Int, body: String)
    case decoding(Error)
    case network(Error)
    case unauthorized
    case missingToken

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid server URL."
        case .http(let s, let b): return "Server error \(s): \(b)"
        case .decoding(let e): return "Could not parse response: \(e.localizedDescription)"
        case .network(let e): return "Network error: \(e.localizedDescription)"
        case .unauthorized: return "Invalid username or password."
        case .missingToken: return "Not logged in."
        }
    }
}

struct AppConfig {
    /// Default server URL. User can override in Settings.
    static let defaultBaseURL = "http://192.168.1.106:8000"
    static let userDefaultsBaseURLKey = "server.baseURL"
    static let userDefaultsTokenKey = "auth.token"
    static let userDefaultsUserKey = "auth.userJSON"
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
    let needs_conversion: Bool
    let spool_path: String?
    let created_at: Int
    let printed_at: Int?

    enum CodingKeys: String, CodingKey {
        case id, user_id, username, filename, mime_type, size_bytes
        case copies, duplex, color_mode
        case page_range, paper_size, orientation, scaling, layout_direction
        case cups_job_id, status, error, needs_conversion, spool_path, created_at, printed_at
    }

    var statusColor: String {
        switch status {
        case "done": return "green"
        case "pending", "processing": return "orange"
        case "pending-conversion": return "blue"
        case "error": return "red"
        case "cancelled": return "gray"
        default: return "primary"
        }
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
