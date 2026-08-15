import SwiftUI

struct MainTabView: View {
    @EnvironmentObject var api: APIClient

    var body: some View {
        TabView {
            NavigationStack {
                PrintView()
            }
            .tabItem {
                Label("Print", systemImage: "doc.viewfinder")
            }

            NavigationStack {
                JobsView()
            }
            .tabItem {
                Label("My Jobs", systemImage: "list.bullet.rectangle")
            }

            NavigationStack {
                SettingsView()
            }
            .tabItem {
                Label("Settings", systemImage: "gearshape")
            }
        }
    }
}
