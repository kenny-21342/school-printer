import SwiftUI

struct LoginView: View {
    @EnvironmentObject var api: APIClient

    @State private var username = ""
    @State private var password = ""
    @State private var showServerSheet = false
    @FocusState private var focused: Field?

    enum Field { case user, pass }

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Spacer().frame(height: 20)

                Image(systemName: "printer.fill")
                    .font(.system(size: 72, weight: .light))
                    .foregroundStyle(.tint)

                VStack(spacing: 4) {
                    Text("School Printer")
                        .font(.title.bold())
                    Text("Sign in to print to the FUJIFILM Xerox (bw)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }

                VStack(spacing: 14) {
                    TextField("Username", text: $username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focused, equals: .user)
                        .submitLabel(.next)
                        .onSubmit { focused = .pass }
                        .textFieldStyle(.roundedBorder)

                    SecureField("Password", text: $password)
                        .focused($focused, equals: .pass)
                        .submitLabel(.go)
                        .onSubmit { Task { await signIn() } }
                        .textFieldStyle(.roundedBorder)
                }
                .padding(.horizontal, 28)

                if let err = api.lastError {
                    Text(err)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }

                Button {
                    Task { await signIn() }
                } label: {
                    HStack {
                        if api.isLoading { ProgressView().controlSize(.small).tint(.white) }
                        Text("Sign In").fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(username.isEmpty || password.isEmpty || api.isLoading)
                .padding(.horizontal, 28)

                Spacer()

                Button("Server settings") {
                    showServerSheet = true
                }
                .font(.footnote)
                .foregroundStyle(.secondary)
                .padding(.bottom, 8)
            }
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear { focused = .user }
            .sheet(isPresented: $showServerSheet) {
                ServerSettingsView()
                    .environmentObject(api)
            }
        }
    }

    private func signIn() async {
        focused = nil
        api.lastError = nil
        do {
            _ = try await api.login(username: username, password: password)
        } catch {
            api.lastError = error.localizedDescription
        }
    }
}

struct ServerSettingsView: View {
    @EnvironmentObject var api: APIClient
    @Environment(\.dismiss) var dismiss
    @State private var url: String = ""

    /// nil when the text isn't a usable http(s) URL.
    private var normalized: String? { AppConfig.normalizeBaseURL(url) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Server URL", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    if !url.trimmingCharacters(in: .whitespaces).isEmpty {
                        if let n = normalized {
                            Label(n, systemImage: "checkmark.circle.fill")
                                .font(.caption).foregroundStyle(.green)
                        } else {
                            Label("Not a valid address", systemImage: "exclamationmark.triangle.fill")
                                .font(.caption).foregroundStyle(.orange)
                        }
                    }
                } footer: {
                    Text("The print server's address — imac.local:8000 on the same Wi‑Fi. If the name doesn't resolve, use the iMac's IP, e.g. 192.168.1.106:8000. http:// is added automatically.")
                }

                Section {
                    Button("Save") {
                        // Only ever store a validated, normalised URL. Saving
                        // raw text let "imac.local:8000" through, which
                        // produces a URL whose *scheme* is the IP address and
                        // fails every request with an opaque error.
                        guard let n = normalized else { return }
                        api.baseURL = n
                        dismiss()
                    }
                    .frame(maxWidth: .infinity)
                    .disabled(normalized == nil)
                }
            }
            .navigationTitle("Server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .onAppear { url = api.baseURL }
        }
    }
}
