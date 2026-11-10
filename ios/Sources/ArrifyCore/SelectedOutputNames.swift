import Foundation

/// Names of selected ports only. Never infer receivers from a transport or UID.
public enum SelectedOutputNames {
    public static func display(_ ports: [(name: String, fallback: String)]) -> String {
        guard !ports.isEmpty else { return "Audio output" }
        return ports.map {
            let name = $0.name.trimmingCharacters(in: .whitespacesAndNewlines)
            return name.isEmpty ? $0.fallback : name
        }.joined(separator: ", ")
    }
}
