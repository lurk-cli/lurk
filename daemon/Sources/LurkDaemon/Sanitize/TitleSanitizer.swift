import Foundation

struct TitleSanitizer {
    private static let patterns: [(NSRegularExpression, String)] = {
        var result: [(NSRegularExpression, String)] = []
        let specs: [(String, String)] = [
            // Email addresses
            ("[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}", "[email]"),
            // Phone numbers (US formats)
            ("\\b\\d{3}[\\-.]?\\d{3}[\\-.]?\\d{4}\\b", "[phone]"),
            // Credit card numbers
            ("\\b\\d{4}[\\- ]?\\d{4}[\\- ]?\\d{4}[\\- ]?\\d{4}\\b", "[card]"),
            // Auth tokens / API keys (long hex or base64 strings, 32+ chars)
            ("\\b[A-Za-z0-9+/\\-_]{32,}={0,2}\\b", "[token]"),
        ]
        for (pattern, replacement) in specs {
            if let regex = try? NSRegularExpression(pattern: pattern) {
                result.append((regex, replacement))
            }
        }
        return result
    }()

    static func sanitize(_ title: String) -> String {
        var result = title
        for (regex, replacement) in patterns {
            let range = NSRange(result.startIndex..., in: result)
            result = regex.stringByReplacingMatches(in: result, range: range, withTemplate: replacement)
        }
        return result
    }
}
