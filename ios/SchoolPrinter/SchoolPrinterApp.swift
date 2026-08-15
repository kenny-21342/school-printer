import SwiftUI

@main
struct SchoolPrinterApp: App {
    @StateObject private var api = APIClient()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(api)
        }
    }
}

struct RootView: View {
    @EnvironmentObject var api: APIClient

    var body: some View {
        Group {
            if api.isLoggedIn {
                MainTabView()
            } else {
                LoginView()
            }
        }
        .task {
            // validate token on launch
            if api.isLoggedIn {
                _ = try? await api.me()
                if !api.isLoggedIn {
                    // me() failed with 401 -> token cleared -> show login
                }
            }
        }
    }
}
