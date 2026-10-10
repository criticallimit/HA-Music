import AppKit
import Foundation

final class WindowBackground: NSView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() { layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor }
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
}

final class PlaylistApp: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate, NSWindowDelegate, NSTextFieldDelegate {
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 710),
        styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    let address = NSTextField(string: "http://homeassistant.local:8099")
    let key = NSSecureTextField(string: "")
    let table = NSTableView()
    let feedback = NSTextField(wrappingLabelWithString: "Musik öffnen und warten, bis deine Mediathek synchronisiert ist.")
    let progress = NSProgressIndicator()
    let newPlaylists = NSButton(checkboxWithTitle: "Fehlende Playlists in HA Music anlegen", target: nil, action: nil)
    let onOpen = NSButton(checkboxWithTitle: "Beim Öffnen synchronisieren", target: nil, action: nil)
    let automatic = NSButton(checkboxWithTitle: "Alle 30 Minuten, solange die App offen ist", target: nil, action: nil)
    var choices: [PlaylistChoice] = []
    var controls: [NSControl] = []
    var busy = false
    var timer: Timer?
    let defaults = UserDefaults.standard
    var smokePath: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildWindow()
        if let path = smokePath {
            choices = [PlaylistChoice(id: "1", name: "Abendmusik", target: "Abendmusik", selected: true),
                       PlaylistChoice(id: "2", name: "Lieblingslieder", target: "Lieblingslieder", selected: false)]
            table.reloadData()
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                let content = self.window.contentView!
                guard let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds) else { exit(1) }
                content.cacheDisplay(in: content.bounds, to: bitmap)
                guard let image = bitmap.representation(using: .png, properties: [:]) else { exit(1) }
                do { try image.write(to: URL(fileURLWithPath: path)); print("Mac UI smoke check passed"); exit(0) }
                catch { exit(1) }
            }
            return
        }
        address.stringValue = defaults.string(forKey: "address") ?? address.stringValue
        newPlaylists.state = defaults.object(forKey: "create") == nil || defaults.bool(forKey: "create") ? .on : .off
        onOpen.state = defaults.object(forKey: "onOpen") == nil || defaults.bool(forKey: "onOpen") ? .on : .off
        automatic.state = defaults.bool(forKey: "automatic") ? .on : .off
        do { key.stringValue = try SyncKeychain.read() } catch { show(error.localizedDescription) }
        loadPlaylists(syncAfter: onOpen.state == .on && !key.stringValue.isEmpty && defaults.data(forKey: "choices") != nil)
    }

    func buildWindow() {
        window.title = "HA Music – Playlist Sync"
        window.minSize = NSSize(width: 800, height: 650)
        window.delegate = self
        window.center()
        let background = WindowBackground(frame: window.contentView!.frame)
        background.wantsLayer = true
        window.contentView = background
        let main = NSStackView()
        main.orientation = .vertical
        main.alignment = .leading
        main.spacing = 12
        main.translatesAutoresizingMaskIntoConstraints = false
        let content = window.contentView!
        content.addSubview(main)
        NSLayoutConstraint.activate([
            main.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            main.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            main.topAnchor.constraint(equalTo: content.topAnchor, constant: 22),
            main.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -22)])
        let title = NSTextField(labelWithString: "Deine Apple-Music-Playlists in HA Music")
        title.font = NSFont.systemFont(ofSize: 24, weight: .semibold)
        main.addArrangedSubview(title)
        let intro = NSTextField(wrappingLabelWithString: "Playlists auswählen und ihre Titel übertragen. Kein Apple-Entwicklerkonto und keine Musikdateien nötig.")
        intro.textColor = .secondaryLabelColor
        main.addArrangedSubview(intro)
        let addressLabel = NSTextField(labelWithString: "HA-Music-Adresse")
        main.addArrangedSubview(addressLabel)
        address.placeholderString = "http://homeassistant.local:8099"
        address.toolTip = "Der optionale HA-Music-Netzwerkport, nicht die Dashboard-Adresse mit Port 8123."
        main.addArrangedSubview(address)
        main.addArrangedSubview(NSTextField(labelWithString: "Synchronisierungsschlüssel"))
        let keyRow = NSStackView(views: [key, button("Neuer Schlüssel", #selector(generateKey)), button("Kopieren", #selector(copyKey))])
        keyRow.orientation = .horizontal
        keyRow.spacing = 8
        key.placeholderString = "Schlüssel aus den HA-Music-Add-on-Optionen"
        key.setContentHuggingPriority(.defaultLow, for: .horizontal)
        key.widthAnchor.constraint(greaterThanOrEqualToConstant: 300).isActive = true
        main.addArrangedSubview(keyRow)
        let help = NSTextField(wrappingLabelWithString: "Einmalig: Schlüssel im HA-Music-Add-on unter „Schlüssel für Mac-Playlist-Synchronisierung“ speichern, Netzwerkport 8099 freigeben und das Add-on neu starten. Nur im Heimnetz verwenden.")
        help.textColor = .secondaryLabelColor
        help.font = NSFont.systemFont(ofSize: 12)
        main.addArrangedSubview(help)
        let tools = NSStackView(views: [button("Playlists neu einlesen", #selector(reload)), button("Alle auswählen", #selector(selectAll)), button("Keine auswählen", #selector(selectNone))])
        tools.orientation = .horizontal
        tools.spacing = 8
        main.addArrangedSubview(tools)
        let checkbox = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("selected"))
        checkbox.title = "Auswahl"
        checkbox.width = 68
        checkbox.minWidth = 68
        checkbox.maxWidth = 68
        table.addTableColumn(checkbox)
        let source = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("name"))
        source.title = "Playlist in Musik"
        source.width = 325
        table.addTableColumn(source)
        let destination = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("target"))
        destination.title = "Name in HA Music (änderbar)"
        destination.width = 350
        table.addTableColumn(destination)
        table.dataSource = self
        table.delegate = self
        table.rowHeight = 30
        table.usesAlternatingRowBackgroundColors = true
        let scroll = NSScrollView()
        scroll.documentView = table
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 155).isActive = true
        main.addArrangedSubview(scroll)
        main.addArrangedSubview(newPlaylists)
        let automaticRow = NSStackView(views: [onOpen, automatic])
        automaticRow.orientation = .horizontal
        automaticRow.spacing = 20
        main.addArrangedSubview(automaticRow)
        let sync = button("Jetzt synchronisieren", #selector(synchronize))
        sync.bezelStyle = .rounded
        sync.keyEquivalent = "\r"
        progress.style = .spinning
        progress.controlSize = .small
        progress.isDisplayedWhenStopped = false
        let footer = NSStackView(views: [sync, progress])
        footer.spacing = 10
        main.addArrangedSubview(footer)
        feedback.font = NSFont.systemFont(ofSize: 12)
        feedback.maximumNumberOfLines = 4
        feedback.setContentCompressionResistancePriority(.required, for: .vertical)
        main.addArrangedSubview(feedback)
        for view in [intro, address, keyRow, help, scroll, feedback] {
            view.translatesAutoresizingMaskIntoConstraints = false
            view.widthAnchor.constraint(equalTo: main.widthAnchor).isActive = true
        }
        controls += [address, key, newPlaylists, onOpen, automatic]
        automatic.target = self
        automatic.action = #selector(updateTimer)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "HA Music Playlist Sync beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem()
        menu.addItem(editItem)
        let edit = NSMenu(title: "Bearbeiten")
        edit.addItem(withTitle: "Kopieren", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Einfügen", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Alles auswählen", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        NSApp.mainMenu = menu
    }

    func button(_ title: String, _ action: Selector) -> NSButton {
        let result = NSButton(title: title, target: self, action: action)
        controls.append(result)
        return result
    }

    func show(_ message: String) { feedback.stringValue = message }

    func setBusy(_ value: Bool) {
        busy = value
        controls.forEach { $0.isEnabled = !value }
        table.reloadData()
        if value { progress.startAnimation(nil) } else { progress.stopAnimation(nil) }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if busy { show("Bitte die laufende Übertragung abwarten."); return false }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if busy { show("Bitte die laufende Übertragung abwarten."); return .terminateCancel }
        return .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    @objc func generateKey() {
        do {
            let alert = NSAlert()
            alert.messageText = "Neuen Schlüssel erzeugen?"
            alert.informativeText = "Den neuen Schlüssel anschließend auch im HA-Music-Add-on speichern. Erst dann kann wieder synchronisiert werden."
            alert.addButton(withTitle: "Erzeugen")
            alert.addButton(withTitle: "Abbrechen")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
            key.stringValue = try SyncKeychain.generate()
            show("Schlüssel erzeugt. Mit „Kopieren“ ins HA-Music-Add-on übernehmen und dort speichern.")
        } catch { show(error.localizedDescription) }
    }

    @objc func copyKey() {
        do {
            try SyncCore.validateKey(key.stringValue)
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(key.stringValue, forType: .string)
            show("Schlüssel kopiert. Im HA-Music-Add-on einfügen, speichern und das Add-on neu starten.")
        } catch { show(error.localizedDescription) }
    }

    @objc func reload() { loadPlaylists(syncAfter: false) }

    func loadPlaylists(syncAfter: Bool) {
        guard !busy else { return }
        setBusy(true)
        show("Lese Playlists aus der Musik-App …")
        let previous = choices
        let saved = defaults.data(forKey: "choices").flatMap { try? JSONDecoder().decode([PlaylistChoice].self, from: $0) } ?? []
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let playlists = try SyncCore.playlists()
                DispatchQueue.main.async {
                    self.choices = playlists.map { playlist in
                        let old = previous.first { $0.id == playlist.id } ?? saved.first { $0.id == playlist.id }
                        return PlaylistChoice(id: playlist.id, name: playlist.name, target: old?.target ?? playlist.name, selected: old?.selected ?? false)
                    }
                    self.setBusy(false)
                    self.show("\(playlists.count) Playlists gefunden. Gewünschte Playlists auswählen; den Namen für HA Music bei Bedarf ändern.")
                    if syncAfter { self.synchronize() }
                }
            } catch {
                DispatchQueue.main.async { self.setBusy(false); self.show(error.localizedDescription) }
            }
        }
    }

    @objc func selectAll() {
        guard !busy else { return }
        for index in choices.indices { choices[index].selected = true }
        table.reloadData()
    }

    @objc func selectNone() {
        guard !busy else { return }
        for index in choices.indices { choices[index].selected = false }
        table.reloadData()
    }

    func numberOfRows(in tableView: NSTableView) -> Int { choices.count }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let choice = choices[row]
        switch tableColumn?.identifier.rawValue {
        case "selected":
            let checkbox = NSButton(checkboxWithTitle: "", target: self, action: #selector(toggleChoice(_:)))
            checkbox.state = choice.selected ? .on : .off
            checkbox.tag = row
            checkbox.isEnabled = !busy
            return checkbox
        case "target":
            let field = NSTextField(string: choice.target)
            field.tag = row
            field.isEnabled = !busy
            field.target = self
            field.delegate = self
            field.action = #selector(changeName(_:))
            field.toolTip = "Doppelklicken, um den Namen für HA Music zu ändern."
            return field
        default:
            let field = NSTextField(labelWithString: choice.name)
            field.lineBreakMode = .byTruncatingTail
            field.toolTip = choice.name + " (" + choice.id + ")"
            return field
        }
    }

    @objc func toggleChoice(_ sender: NSButton) { choices[sender.tag].selected = sender.state == .on }
    @objc func changeName(_ sender: NSTextField) { choices[sender.tag].target = sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) }

    func controlTextDidEndEditing(_ notification: Notification) {
        if let field = notification.object as? NSTextField, choices.indices.contains(field.tag) { changeName(field) }
    }

    @objc func updateTimer() {
        timer?.invalidate()
        timer = nil
        if automatic.state == .on {
            timer = Timer.scheduledTimer(withTimeInterval: 1800, repeats: true) { _ in self.synchronize() }
        }
        defaults.set(automatic.state == .on, forKey: "automatic")
    }

    @objc func synchronize() {
        guard !busy else { return }
        window.makeFirstResponder(nil) // Commit any edited destination name before reading choices.
        do {
            let endpoint = try SyncCore.endpoint(address.stringValue)
            let token = key.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            try SyncCore.validateKey(token)
            let selected = choices.filter { $0.selected }
            guard !selected.isEmpty, selected.count <= 50 else { throw SyncFailure(message: "Bitte 1 bis 50 Playlists auswählen.") }
            guard Set(selected.map { $0.target }).count == selected.count else { throw SyncFailure(message: "Bitte für jede Playlist einen eindeutigen Namen in HA Music vergeben.") }
            try SyncKeychain.save(token)
            defaults.set(address.stringValue, forKey: "address")
            defaults.set(try JSONEncoder().encode(choices), forKey: "choices")
            defaults.set(newPlaylists.state == .on, forKey: "create")
            defaults.set(onOpen.state == .on, forKey: "onOpen")
            defaults.set(automatic.state == .on, forKey: "automatic")
            let create = newPlaylists.state == .on
            setBusy(true)
            show("Übertrage \(selected.count) Playlists …")
            DispatchQueue.global(qos: .userInitiated).async {
                var success = 0
                var failures: [String] = []
                var uploadedCovers = Set<String>()
                var missingCovers = 0
                for (index, choice) in selected.enumerated() {
                    DispatchQueue.main.async { self.show("\(index + 1)/\(selected.count): \(choice.name) …") }
                    do {
                        let tracks = try SyncCore.withArtworks(SyncCore.tracks(id: choice.id), playlistID: choice.id)
                        let coverEndpoint = endpoint.deletingLastPathComponent().appendingPathComponent("playlist-artwork-sync")
                        for track in tracks {
                            if let cover = track.cover, !uploadedCovers.contains(cover), let payload = try SyncCore.artworkPayload(track) {
                                _ = try SyncTransport().send(endpoint: coverEndpoint, key: token, payload: payload)
                                uploadedCovers.insert(cover)
                            }
                        }
                        let data = try SyncCore.payload(name: choice.target, tracks: tracks, create: create)
                        _ = try SyncTransport().send(endpoint: endpoint, key: token, payload: data)
                        missingCovers += tracks.filter { $0.cover == nil }.count
                        success += 1
                    } catch { failures.append(choice.name + ": " + error.localizedDescription) }
                }
                let message = failures.isEmpty ? "\(success) Playlists mit \(uploadedCovers.count) lokalen Covern synchronisiert. \(missingCovers) Titel ohne verfügbares Cover. Titellisten in HA Music erneut öffnen." :
                    "\(success) erfolgreich, \(failures.count) fehlgeschlagen. Bisherige Listen fehlgeschlagener Übertragungen bleiben erhalten.\n" + failures.prefix(2).joined(separator: "\n")
                DispatchQueue.main.async {
                    self.setBusy(false)
                    self.show(message)
                    self.updateTimer()
                }
            }
        } catch { show(error.localizedDescription) }
    }
}

if CommandLine.arguments.contains("--self-test") {
    do {
        let endpoint = try SyncCore.endpoint("http://homeassistant.local:8099")
        assert(endpoint.absoluteString == "http://homeassistant.local:8099/api/playlist-sync")
        let invalid = ["http://public.example", "http://user:secret@homeassistant.local", "https://host/path", "https://host/?key=secret"]
        for address in invalid {
            do { _ = try SyncCore.endpoint(address); fatalError("Invalid endpoint accepted") }
            catch is SyncFailure { }
        }
        let tracks = [MusicTrack(name: "Grüße 🎵", artist: "Björk", album: "Debut", albumArtist: "Björk"), MusicTrack(name: "Again", artist: "Band"), MusicTrack(name: "Again", artist: "Band")]
        let data = try SyncCore.payload(name: "Mix", tracks: tracks, create: true)
        let object = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        assert((object["tracks"] as! [[String: Any]]).count == 3)
        let first = (object["tracks"] as! [[String: Any]])[0]
        assert(first["album"] as! String == "Debut")
        assert(first["album_artist"] as! String == "Björk")
        let decoded = try JSONDecoder().decode([MusicTrack].self, from: JSONEncoder().encode(tracks))
        assert(decoded[0].album == "Debut" && decoded[0].albumArtist == "Björk")
        assert(object["create"] as! Bool)
        let fixture = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAgCAIAAAAt/+nTAAAATElEQVR4nNXOQREAMAjAsK7+PTMRPLhGQR4MZRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncRIncV4Htj4imAE/t2tu2AAAAABJRU5ErkJggg==")!
        let thumbnail = SyncCore.thumbnail(fixture)!
        let decodedImage = NSBitmapImageRep(data: thumbnail)!
        assert(decodedImage.pixelsWide == 320 && decodedImage.pixelsHigh == 320)
        assert(decodedImage.colorAt(x: 160, y: 160)!.usingColorSpace(.deviceRGB)!.blueComponent > 0.8)
        assert(SyncCore.thumbnail(Data("not an image".utf8)) == nil)
        var illustrated = tracks[0]
        illustrated.artworkData = thumbnail; illustrated.cover = SyncCore.coverIdentity(thumbnail); illustrated.localCovers = true
        let imagePayload = try JSONSerialization.jsonObject(with: SyncCore.artworkPayload(illustrated)!) as! [String: String]
        assert(Data(base64Encoded: imagePayload["data"]!) == thumbnail && imagePayload["cover"] == illustrated.cover)
        let illustratedPayload = try JSONSerialization.jsonObject(with: SyncCore.payload(name: "Mix", tracks: [illustrated], create: false)) as! [String: Any]
        let illustratedTrack = (illustratedPayload["tracks"] as! [[String: Any]])[0]
        assert((illustratedTrack["cover"] as? String) == illustrated.cover && illustratedTrack["artworkData"] == nil)
        let scriptFolder = FileManager.default.temporaryDirectory.appendingPathComponent("ha-music-script-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: scriptFolder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: scriptFolder) }
        let scriptFile = scriptFolder.appendingPathComponent("artwork.applescript")
        try SyncCore.artworkScript(folder: scriptFolder, playlistID: "0123456789ABCDEF").write(to: scriptFile, atomically: true, encoding: .utf8)
        let compiler = Process()
        compiler.executableURL = URL(fileURLWithPath: "/usr/bin/osacompile")
        compiler.arguments = ["-o", scriptFolder.appendingPathComponent("artwork.scpt").path, scriptFile.path]
        try compiler.run(); compiler.waitUntilExit()
        assert(compiler.terminationStatus == 0, "Music artwork export script must compile")
        do { _ = try SyncCore.payload(name: "Mix", tracks: [], create: false); fatalError("Empty playlist accepted") } catch is SyncFailure { }
        try SyncCore.validateKey(SyncKeychain.generate())
        let value = try SyncCore.quoted("\"; throw new Error('injected')")
        assert(value.hasPrefix("\"\\\""))
        print("Native Mac core checks passed")
        exit(0)
    } catch { fputs("Mac core checks failed\n", stderr); exit(1) }
}

// Used only by the CI loopback fixture; normal users launch the app in Finder.
if CommandLine.arguments.count == 5 && ["--transport-test", "--artwork-transport-test"].contains(CommandLine.arguments[1]) {
    do {
        var endpoint = try SyncCore.endpoint(CommandLine.arguments[2])
        if CommandLine.arguments[1] == "--artwork-transport-test" {
            endpoint = endpoint.deletingLastPathComponent().appendingPathComponent("playlist-artwork-sync")
        }
        let payload = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[4]))
        let result = try SyncTransport().send(endpoint: endpoint, key: CommandLine.arguments[3], payload: payload)
        print("Confirmed \(result.tracks) tracks")
        exit(0)
    } catch { fputs((error.localizedDescription + "\n").cString(using: .utf8)!, stderr); exit(1) }
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = PlaylistApp()
if let index = CommandLine.arguments.firstIndex(of: "--ui-smoke"), CommandLine.arguments.count > index + 1 {
    delegate.smokePath = CommandLine.arguments[index + 1]
}
application.delegate = delegate
application.run()
