import Foundation
import Security

struct SyncFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct MusicPlaylist: Codable {
    let id: String
    let name: String
}

struct MusicTrack: Codable {
    let name: String
    let artist: String
    let album: String?
    let albumArtist: String?
    enum CodingKeys: String, CodingKey { case name, artist, album; case albumArtist = "album_artist" }
    init(name: String, artist: String, album: String? = nil, albumArtist: String? = nil) {
        self.name = name; self.artist = artist; self.album = album; self.albumArtist = albumArtist
    }
}

struct PlaylistChoice: Codable {
    var id: String
    var name: String
    var target: String
    var selected: Bool
}

struct SyncPayload: Encodable {
    let name: String
    let tracks: [MusicTrack]
    let create: Bool
}

struct SyncResult: Decodable {
    let ok: Bool
    let changed: Bool
    let tracks: Int
    let created: Bool?
}

enum SyncCore {
    static func endpoint(_ text: String) throws -> URL {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var url = URLComponents(string: value), let scheme = url.scheme,
              ["http", "https"].contains(scheme), let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/", url.port == nil || (1...65535).contains(url.port!) else {
            throw SyncFailure(message: "Bitte eine Adresse wie http://homeassistant.local:8099 eingeben, ohne weiteren Pfad.")
        }
        if scheme == "http" {
            let lower = host.lowercased()
            let octets = lower.split(separator: ".").compactMap { Int($0) }
            let localIPv4 = octets.count == 4 && octets.allSatisfy { (0...255).contains($0) } &&
                (octets[0] == 10 || octets[0] == 127 ||
                 (octets[0] == 192 && octets[1] == 168) ||
                 (octets[0] == 172 && (16...31).contains(octets[1])) ||
                 (octets[0] == 169 && octets[1] == 254))
            guard localIPv4 || lower == "localhost" || lower == "::1" || lower == "[::1]" || lower.hasSuffix(".local") else {
                throw SyncFailure(message: "HTTP bitte nur mit einer lokalen IP oder .local-Adresse nutzen. Für andere Adressen HTTPS verwenden.")
            }
        }
        url.path = "/api/playlist-sync"
        guard let result = url.url else { throw SyncFailure(message: "Die Adresse ist ungültig.") }
        return result
    }

    static func validateKey(_ key: String) throws {
        guard key.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil else {
            throw SyncFailure(message: "Bitte den Synchronisierungsschlüssel eingeben oder mit „Neuer Schlüssel“ erzeugen.")
        }
    }

    static func quoted(_ value: String) throws -> String {
        let data = try JSONEncoder().encode(value)
        return String(decoding: data, as: UTF8.self)
    }

    static func payload(name: String, tracks: [MusicTrack], create: Bool) throws -> Data {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 200,
              !name.unicodeScalars.contains(where: { $0.value < 32 }), tracks.count <= 1000,
              tracks.allSatisfy({ track in
                  [track.name, track.artist].allSatisfy { text in
                      !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && text.count <= 200 &&
                          !text.unicodeScalars.contains(where: { $0.value < 32 })
                  } && [track.album, track.albumArtist].allSatisfy { text in
                      guard let text = text else { return true }
                      return text.count <= 200 && !text.unicodeScalars.contains(where: { $0.value < 32 })
                  }
              }) else { throw SyncFailure(message: "Playlistname oder Titelinformationen sind unvollständig. Höchstens 1000 Titel pro Playlist sind erlaubt.") }
        guard !tracks.isEmpty else {
            throw SyncFailure(message: "Die Playlist ist leer. Sie wird vorsorglich nicht übertragen. Bitte die Mediathek-Synchronisierung in Musik prüfen.")
        }
        let data = try JSONEncoder().encode(SyncPayload(name: name, tracks: tracks, create: create))
        guard data.count <= 2097152 else { throw SyncFailure(message: "Die Playlist ist zu groß für eine Übertragung.") }
        return data
    }

    static func runMusic(_ script: String) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-l", "JavaScript", "-"]
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        try process.run()
        let timeout = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 120, execute: timeout)
        input.fileHandleForWriting.write(Data(script.utf8))
        input.fileHandleForWriting.closeFile()
        // Drain both streams while the subprocess runs, including large playlists.
        let errorGroup = DispatchGroup()
        errorGroup.enter()
        DispatchQueue.global().async {
            _ = errors.fileHandleForReading.readDataToEndOfFile()
            errorGroup.leave()
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        errorGroup.wait()
        timeout.cancel()
        guard process.terminationStatus == 0 else {
            throw SyncFailure(message: "Die Musik-App konnte nicht ausgelesen werden. Bitte Musik öffnen und unter Systemeinstellungen → Datenschutz & Sicherheit → Automation den Zugriff erlauben. Bei Bedarf die App erneut öffnen.")
        }
        guard data.count <= 2097152 else { throw SyncFailure(message: "Die Antwort der Musik-App ist zu groß.") }
        return data
    }

    static func playlists() throws -> [MusicPlaylist] {
        let script = """
        const music = Application('Music');
        const lists = music.userPlaylists();
        if (lists.length > 5000) throw new Error('Too many playlists');
        JSON.stringify(lists.map(p => ({id: p.persistentID(), name: p.name()})));
        """
        return try JSONDecoder().decode([MusicPlaylist].self, from: runMusic(script))
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func tracks(id: String) throws -> [MusicTrack] {
        let script = """
        const music = Application('Music');
        const matches = music.userPlaylists.whose({persistentID: \(try quoted(id))})();
        if (matches.length !== 1) throw new Error('Playlist missing');
        const tracks = matches[0].tracks();
        if (tracks.length > 1000) throw new Error('Too many tracks');
        function optional(read) { try { return read() || ''; } catch (_) { return ''; } }
        JSON.stringify(tracks.map(t => ({name: t.name(), artist: t.artist(), album: optional(() => t.album()), album_artist: optional(() => t.albumArtist())})));
        """
        return try JSONDecoder().decode([MusicTrack].self, from: runMusic(script))
    }
}

enum SyncKeychain {
    static let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.ha-music.playlist-app", kSecAttrAccount as String: "playlist-sync"]

    static func read() throws -> String {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return "" }
        guard status == errSecSuccess, let data = result as? Data, let text = String(data: data, encoding: .utf8) else {
            throw SyncFailure(message: "Der Schlüsselbund konnte nicht gelesen werden. Bitte den Schlüssel erneut eingeben.")
        }
        return text
    }

    static func save(_ key: String) throws {
        let attributes = [kSecValueData as String: Data(key.utf8)]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = Data(key.utf8)
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else {
                throw SyncFailure(message: "Der Schlüssel konnte nicht im Schlüsselbund gespeichert werden.")
            }
        } else if status != errSecSuccess {
            throw SyncFailure(message: "Der Schlüssel konnte nicht im Schlüsselbund gespeichert werden.")
        }
    }

    static func generate() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw SyncFailure(message: "Es konnte kein sicherer Schlüssel erzeugt werden.")
        }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

final class SyncTransport: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // A redirect must never receive the key.
    }

    func send(endpoint: URL, key: String, payload: Data) throws -> SyncResult {
        try SyncCore.validateKey(key)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 35
        configuration.httpShouldSetCookies = false
        configuration.connectionProxyDictionary = ["HTTPEnable": 0, "HTTPSEnable": 0,
            "ProxyAutoConfigEnable": 0, "ProxyAutoDiscoveryEnable": 0]
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.httpBody = payload
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
        let signal = DispatchSemaphore(value: 0)
        var result: Result<SyncResult, Error> = .failure(SyncFailure(message: "Zeitüberschreitung bei der Übertragung."))
        session.dataTask(with: request) { data, response, error in
            defer { signal.signal() }
            if error != nil {
                result = .failure(SyncFailure(message: "HA Music ist nicht erreichbar. Bitte Adresse und Netzwerkport prüfen."))
                return
            }
            guard let response = response as? HTTPURLResponse else { return }
            guard response.statusCode == 200, let data = data, data.count <= 8192 else {
                let message: String
                switch response.statusCode {
                case 403: message = "Schlüssel nicht akzeptiert. Bitte im Add-on speichern und das Add-on neu starten."
                case 400: message = "HA Music hat die Playlist abgewiesen. Namen, Titelinformationen und die Grenze von 50 Favoriten prüfen. Fehlende Playlists gegebenenfalls automatisch anlegen lassen."
                case 404: message = "Der Synchronisierungsendpunkt fehlt. Bitte HA Music auf den aktuellen main-Stand aktualisieren."
                case 300..<400: message = "Die Adresse leitet auf eine andere Seite um. Bitte den direkten HA-Music-Netzwerkport verwenden."
                default: message = "HA Music konnte die Playlist nicht speichern (HTTP \(response.statusCode))."
                }
                result = .failure(SyncFailure(message: message))
                return
            }
            do {
                let decoded = try JSONDecoder().decode(SyncResult.self, from: data)
                guard decoded.ok else { throw SyncFailure(message: "HA Music hat die Übertragung nicht bestätigt.") }
                result = .success(decoded)
            } catch { result = .failure(SyncFailure(message: "HA Music hat keine gültige Bestätigung geliefert.")) }
        }.resume()
        signal.wait()
        return try result.get()
    }
}
