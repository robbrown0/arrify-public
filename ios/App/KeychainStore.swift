import Foundation
import Security
import ArrifyCore

actor KeychainStore: CredentialStore {
    private let service = "com.robnets.arrify.native-session"
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: "active"]
    }
    func load() throws -> Credential? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &value)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = value as? Data else { throw KeychainFailure() }
        let credential = try JSONDecoder().decode(Credential.self, from: data)
        SavedServerAddresses.remember(credential)
        return credential
    }
    func save(_ credential: Credential) throws {
        let values: [String: Any] = [kSecValueData as String: try JSONEncoder().encode(credential),
                                    kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, values as CFDictionary)
        if status == errSecItemNotFound {
            guard SecItemAdd(query.merging(values) { _, new in new } as CFDictionary, nil) == errSecSuccess else { throw KeychainFailure() }
        } else if status != errSecSuccess { throw KeychainFailure() }
        SavedServerAddresses.remember(credential)
    }
    func clear() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainFailure() }
    }
}
private struct KeychainFailure: LocalizedError {
    var errorDescription: String? { "Unable to access secure sign-in storage. Unlock your device and try again." }
}

// Addresses are preferences, not authentication. Keep them across token expiry
// and sign-out; login still verifies the server and creates a new credential.
enum SavedServerAddresses {
    static func remember(_ credential: Credential, defaults: UserDefaults = .standard) {
        defaults.set([
            "public": credential.endpoint,
            "home": credential.homeEndpoint ?? ""
        ], forKey: "savedServerAddresses")
    }
    static var value: [String: String]? {
        UserDefaults.standard.dictionary(forKey: "savedServerAddresses") as? [String: String]
    }
}
