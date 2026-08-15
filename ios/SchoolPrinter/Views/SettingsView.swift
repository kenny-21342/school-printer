import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var api: APIClient
    @State private var showServerSheet = false
    @State private var jobsCount = 0

    var body: some View {
        List {
            Section {
                if let u = api.currentUser {
                    HStack(spacing: 14) {
                        ZStack {
                            Circle().fill(.tint.opacity(0.15))
                                .frame(width: 48, height: 48)
                            Text(String(u.username.prefix(1)).uppercased())
                                .font(.title3.weight(.bold))
                                .foregroundStyle(.tint)
                        }
                        VStack(alignment: .leading) {
                            Text(u.display_name ?? u.username).font(.headline)
                            Text(u.username).font(.caption).foregroundStyle(.secondary)
                            if u.is_admin {
                                Text("Administrator")
                                    .font(.caption2.weight(.semibold))
                                    .padding(.horizontal, 6).padding(.vertical, 2)
                                    .background(.tint.opacity(0.15), in: Capsule())
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }
            }

            Section("Server") {
                LabeledContent("Address") { Text(api.baseURL).foregroundStyle(.secondary) }
                Button("Change server…") { showServerSheet = true }
            }

            Section {
                Button(role: .destructive) {
                    api.logout()
                } label: {
                    Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                }
            }
        }
        .navigationTitle("Settings")
        .sheet(isPresented: $showServerSheet) {
            ServerSettingsView().environmentObject(api)
        }
    }
}
