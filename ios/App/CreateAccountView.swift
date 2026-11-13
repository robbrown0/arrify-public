import SwiftUI

struct AccountPasswordField: View {
    let title: String
    @Binding var text: String
    var identifier = ""
    var newPassword = true
    @State private var visible = false
    var body: some View {
        HStack {
            Group {
                if visible { TextField(title, text: $text) }
                else { SecureField(title, text: $text) }
            }.textContentType(newPassword ? .newPassword : .password)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .accessibilityIdentifier(identifier)
            Button { visible.toggle() } label: {
                Image(systemName: visible ? "eye.slash" : "eye").frame(minWidth: 44, minHeight: 44)
            }.buttonStyle(.borderless).accessibilityLabel(visible ? "Hide password" : "Show password")
        }
    }
}

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
    private var lengthMet: Bool { (15...128).contains(password.count) }
    private var matches: Bool { !confirmation.isEmpty && password == confirmation }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Image("BrandLogo").resizable().scaledToFit().frame(height: 64).frame(maxWidth: .infinity)
                    Text(created ? "Check your inbox" : "Your next favorite starts here").font(.title2.bold())
                    Text(created ? "Verify your email, then wait for administrator approval before signing in." : "Create your Arrify account to make the music yours.").foregroundStyle(.secondary)
                }
                if !created {
                    Section("Your details") {
                        TextField("Username", text: $username).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("newUsername")
                        TextField("Email", text: $email).textContentType(.emailAddress).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("newEmail")
                    }
                    Section("Keep it secure") {
                        AccountPasswordField(title: "Password", text: $password, identifier: "newPassword")
                        Label(lengthMet ? "Length requirement met" : "Use 15–128 characters", systemImage: lengthMet ? "checkmark.circle.fill" : "circle").font(.footnote).foregroundStyle(lengthMet ? .green : .secondary)
                        Text("Use a unique password or a few unrelated words. Password managers and paste are welcome.").font(.footnote).foregroundStyle(.secondary)
                        AccountPasswordField(title: "Confirm password", text: $confirmation, identifier: "confirmPassword")
                        if !confirmation.isEmpty { Label(matches ? "Passwords match" : "Passwords don’t match yet", systemImage: matches ? "checkmark.circle.fill" : "circle").font(.footnote).foregroundStyle(matches ? .green : .secondary) }
                    }
                    Section {
                        Button {
                            busy = true; message = nil
                            Task {
                                do { let response = try await model.api.register(endpoint: model.endpoint, username: username.trimmingCharacters(in: .whitespacesAndNewlines), email: email.trimmingCharacters(in: .whitespacesAndNewlines), password: password); message = response.message; created = true; password = ""; confirmation = "" }
                                catch { message = error.localizedDescription }
                                busy = false
                            }
                        } label: { HStack { Spacer(); if busy { ProgressView() }; Text(busy ? "Creating…" : "Create account").bold(); Spacer() } }
                        .disabled(busy || username.count < 3 || username.count > 40 || !email.contains("@") || !lengthMet || !matches).accessibilityIdentifier("submitAccount")
                    }
                }
                if let message { Section { Text(message).accessibilityIdentifier("registrationMessage") } }
                if created {
                    Button("Resend verification email") { Task { do { message = try await model.api.requestAccountEmail(endpoint: model.endpoint, identifier: email, purpose: "verify").message } catch { message = error.localizedDescription } } }
                    Button("Return to sign in") { dismiss() }
                }
            }.navigationTitle("Create account").navigationBarTitleDisplayMode(.inline).toolbar { Button("Done") { dismiss() }.disabled(busy) }
        }.interactiveDismissDisabled(busy)
    }
}

struct AccountRecoveryView: View {
    let model: AppModel
    @State private var identifier = ""
    @State private var busy = false
    @State private var message: String?
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Image("BrandLogo").resizable().scaledToFit().frame(height: 64).frame(maxWidth: .infinity)
                    Text("Back to your music").font(.title2.bold())
                    Text("We’ll email a secure link. Open it to choose a new password, then return here to sign in.").foregroundStyle(.secondary)
                }
                Section { TextField("Email or username", text: $identifier).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled() }
                Section {
                    Button(busy ? "Sending…" : "Send reset link") { request("reset") }.disabled(busy || identifier.isEmpty)
                    Button("Resend verification email") { request("verify") }.disabled(busy || identifier.isEmpty)
                }
                if let message { Section { Text(message).accessibilityIdentifier("recoveryMessage") } }
            }.navigationTitle("Account access").navigationBarTitleDisplayMode(.inline)
        }
    }
    private func request(_ purpose: String) {
        busy = true
        Task { do { message = try await model.api.requestAccountEmail(endpoint: model.endpoint, identifier: identifier, purpose: purpose).message } catch { message = error.localizedDescription }; busy = false }
    }
}
