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
        // sessionID changes whenever the session is established or cleared,
        // which forces this Group to re-evaluate api.isLoggedIn.
        .id(api.sessionID)
        .task {
            // Validate the stored token on launch. A 401 inside validateSession
            // clears the token (see APIClient.handle), so an expired session
            // lands on LoginView instead of a tab view where every screen errors.
            await api.validateSession()
        }
    }
}
