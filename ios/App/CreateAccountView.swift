import SwiftUI

struct CreateAccountView: View {
    let model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var username = ""
    @State private var email = ""
    @State private var password = ""
    @State private var confirmation = ""
    @State private var busy = false
    @State private var message: String?
    @State private var created = false
    var body: some View {
        NavigationStack {
            Form {
                Section { Text("Create an account on \(model.endpoint)"); Text("An administrator must approve your account before you can listen.").font(.footnote).foregroundStyle(.secondary) }
                if !created {
                    Section("Account") {
                        TextField("Username", text: $username).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("newUsername")
                        TextField("Email", text: $email).textContentType(.emailAddress).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("newEmail")
                        SecureField("Password · at least 12 characters", text: $password).textContentType(.newPassword).accessibilityIdentifier("newPassword")
                        SecureField("Confirm password", text: $confirmation).textContentType(.newPassword).accessibilityIdentifier("confirmPassword")
                    }
                    Button(busy ? "Creating…" : "Create account") {
                        busy = true; message = nil
                        Task {
                            do { let response = try await model.api.register(endpoint: model.endpoint, username: username.trimmingCharacters(in: .whitespacesAndNewlines), email: email.trimmingCharacters(in: .whitespacesAndNewlines), password: password); message = response.message; created = true; password = ""; confirmation = "" }
                            catch { message = error.localizedDescription }
                            busy = false
                        }
                    }.disabled(busy || username.count < 3 || username.count > 40 || !email.contains("@") || password.count < 12 || password.count > 128 || password != confirmation).accessibilityIdentifier("submitAccount")
                }
                if let message { Text(message).accessibilityIdentifier("registrationMessage") }
                if created { Button("Return to sign in") { dismiss() } }
            }.navigationTitle("Create account").toolbar { Button("Done") { dismiss() }.disabled(busy) }
        }.interactiveDismissDisabled(busy)
    }
}
