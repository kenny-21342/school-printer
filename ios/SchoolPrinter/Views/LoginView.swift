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

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Server URL", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                } footer: {
                    Text("The Pi's address. On the school Wi‑Fi this will be the Pi's school IP.")
                }

                Section {
                    Button("Save") {
                        api.baseURL = url.trimmingCharacters(in: .whitespaces)
                        dismiss()
                    }
                    .frame(maxWidth: .infinity)
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
