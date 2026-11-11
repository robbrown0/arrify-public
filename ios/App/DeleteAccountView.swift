import SwiftUI

struct DeleteAccountView: View {
    let model: AppModel
    let signedIn: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var username = ""
    @State private var password = ""
    @State private var confirming = false
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        Form {
            Section("Permanent deletion") {
                Text("Delete your account on \(model.endpoint)")
                Text("This permanently removes your profile, liked songs, personal and shared playlists, listening and search history, requests, hosted parties, and linked-service credentials. All devices will be signed out. This cannot be undone.")
                Text("Shared server music and other people’s accounts are not deleted. Deleting the last administrator account leaves server administration to the server operator.").font(.footnote).foregroundStyle(.secondary)
                Text("Backup copies expire under your server operator’s retention policy. Deleted accounts are blocked from restoration by the server’s deletion record.").font(.footnote).foregroundStyle(.secondary)
            }
            Section("Verify your identity") {
                if !signedIn {
                    Text("You can delete an account even while it is awaiting approval or frozen.").font(.footnote)
                    TextField("Username", text: $username).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("deletionUsername")
                }
                SecureField("Current password", text: $password).textContentType(.password).accessibilityIdentifier("deletionPassword")
            }
            if let error { Section { Text(error).foregroundStyle(.orange).accessibilityIdentifier("deletionError") } }
            Section {
                Button(busy ? "Deleting…" : "Delete account", role: .destructive) { confirming = true }
                    .disabled(busy || password.isEmpty || (!signedIn && username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                    .accessibilityIdentifier("requestAccountDeletion")
            }
        }.navigationTitle("Delete account")
            .toolbar { if !signedIn { Button("Cancel", role: .cancel) { dismiss() }.disabled(busy) } }
            .interactiveDismissDisabled(busy)
            .navigationBarBackButtonHidden(busy)
            .confirmationDialog("Permanently delete your account?", isPresented: $confirming, titleVisibility: .visible) {
                Button("Permanently delete account", role: .destructive) {
                    busy = true; error = nil
                    Task {
                        do {
                            if signedIn { _ = try await model.api.deleteAccount(password: password) }
                            else { _ = try await model.api.deleteAccount(endpoint: model.endpoint, username: username.trimmingCharacters(in: .whitespacesAndNewlines), password: password) }
                            password = ""
                            if !signedIn { dismiss() }
                            await model.finishAccountDeletion()
                        } catch { self.error = error.localizedDescription; password = "" }
                        busy = false
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: { Text("Your account and personal data will be permanently removed. This cannot be undone.") }
    }
}
