// Read-only native Save-panel evidence. No capability or Save dispatch here.
// AX/filesystem reads are repeated, not atomic; ABA remains possible.
import Foundation
import ApplicationServices

enum NativeSavePanelError: Error { case unavailable(String) }
struct NativeSaveDirectoryResource: Equatable {
    let path: String
    let device: Int64
    let inode: UInt64
}
struct NativeSavePanelWitness {
    let filename: String
    let format: String?
    let formatValue: String?
    let keepChanges: Int?
    let directory: NativeSaveDirectoryResource
    let resources: [NativeSaveDirectoryResource]
    let identities: [AXUIElement]
    let commit: AXUIElement
}

final class NativeSavePanelReader {
    typealias AttributeReader = (AXUIElement, String) throws -> (AXError, AnyObject?)
    let readAttribute: AttributeReader
    let directoryResource: (AnyObject) throws -> NativeSaveDirectoryResource
    let check: () throws -> Void
    let maxNodes: Int
    let maxSeconds: Double
    private var remaining = 0
    private var deadline = 0.0

    init(readAttribute: @escaping AttributeReader,
         directoryResource: @escaping (AnyObject) throws -> NativeSaveDirectoryResource,
         check: @escaping () throws -> Void = {}, maxNodes: Int = 5000,
         maxSeconds: Double = 3) throws {
        guard maxNodes > 0, maxSeconds.isFinite, maxSeconds > 0, maxSeconds <= 30 else {
            throw NativeSavePanelError.unavailable("invalid observation budget")
        }
        self.readAttribute = readAttribute
        self.directoryResource = directoryResource
        self.check = check
        self.maxNodes = maxNodes
        self.maxSeconds = maxSeconds
    }
    private func charge(_ amount: Int = 1) throws {
        try check()
        remaining -= amount
        guard remaining >= 0, ProcessInfo.processInfo.systemUptime <= deadline else {
            throw NativeSavePanelError.unavailable("save panel observation budget exceeded")
        }
    }
    private func value(_ element: AXUIElement, _ name: String, optional: Bool = false) throws -> AnyObject? {
        try charge()
        let (error, result) = try readAttribute(element, name)
        try charge(0)
        if error == .success, let result { return result }
        if optional && (error == .attributeUnsupported || error == .noValue) { return nil }
        throw NativeSavePanelError.unavailable(name + " unavailable")
    }
    private func text(_ element: AXUIElement, _ name: String, optional: Bool = false) throws -> String? {
        guard let raw = try value(element, name, optional: optional) else { return nil }
        guard CFGetTypeID(raw) == CFStringGetTypeID(), let text = raw as? String else {
            throw NativeSavePanelError.unavailable(name + " is not a string")
        }
        return text
    }
    private func array(_ element: AXUIElement, _ name: String, optional: Bool = false) throws -> [AXUIElement] {
        guard let raw = try value(element, name, optional: optional) else { return [] }
        guard CFGetTypeID(raw) == CFArrayGetTypeID(), let items = raw as? NSArray else {
            throw NativeSavePanelError.unavailable(name + " is not an array")
        }
        try charge(items.count)
        return try items.map { item in
            guard CFGetTypeID(item as CFTypeRef) == AXUIElementGetTypeID() else {
                throw NativeSavePanelError.unavailable("non-native AX array child")
            }
            return item as! AXUIElement
        }
    }
    private func children(_ element: AXUIElement) throws -> [AXUIElement] {
        try array(element, "AXChildren", optional: true)
    }
    private func role(_ element: AXUIElement) throws -> String {
        guard let role = try text(element, "AXRole"), !role.isEmpty else {
            throw NativeSavePanelError.unavailable("invalid AX role")
        }
        return role
    }
    private func parent(_ child: AXUIElement, is expected: AXUIElement) throws {
        guard let raw = try value(child, "AXParent"), CFGetTypeID(raw) == AXUIElementGetTypeID(),
              CFEqual(raw, expected) else {
            throw NativeSavePanelError.unavailable("save panel hierarchy changed")
        }
    }
    private func unique<T>(_ values: [T], _ message: String) throws -> T {
        guard values.count == 1 else { throw NativeSavePanelError.unavailable(message) }
        return values[0]
    }
    private func sheet(_ window: AXUIElement) throws -> AXUIElement {
        let sheets = try children(window).filter { try role($0) == "AXSheet" }
        let result = try unique(sheets, "save sheet absent or ambiguous")
        try parent(result, is: window)
        guard try text(result, "AXIdentifier") == "save-panel" else {
            throw NativeSavePanelError.unavailable("unsupported save sheet")
        }
        return result
    }
    private func directory(_ window: AXUIElement) throws -> ([AXUIElement], [NativeSaveDirectoryResource]) {
        let sheet = try sheet(window)
        var identities = [window, sheet]
        var stack: [(AXUIElement, Int, [AXUIElement])] = [(sheet, 0, [sheet])]
        var seen: [AXUIElement] = []
        var browsers: [(AXUIElement, [AXUIElement])] = []
        while let (node, depth, ancestry) = stack.popLast() {
            try charge()
            guard depth <= 32, !seen.contains(where: { CFEqual($0, node) }) else {
                throw NativeSavePanelError.unavailable("cyclic or excessive save hierarchy")
            }
            seen.append(node)
            let kind = try role(node)
            if kind == "AXBrowser" {
                guard try text(node, "AXIdentifier") == "ColumnView" else {
                    throw NativeSavePanelError.unavailable("unsupported save browser")
                }
                browsers.append((node, ancestry)); continue
            }
            if ["AXList", "AXOutline"].contains(kind) { continue }
            for child in try children(node).reversed() {
                try parent(child, is: node)
                stack.append((child, depth + 1, ancestry + [child]))
            }
        }
        let (browser, ancestry) = try unique(browsers, "column browser absent or ambiguous")
        identities.append(contentsOf: ancestry.dropFirst())
        let container = try unique(children(browser), "column container absent or ambiguous")
        guard try role(container) == "AXScrollArea" else {
            throw NativeSavePanelError.unavailable("unsupported column container")
        }
        try parent(container, is: browser); identities.append(container)
        var columns: [AXUIElement] = []
        for child in try children(container) {
            let kind = try role(child)
            if kind == "AXScrollArea" { columns.append(child) }
            else if kind != "AXScrollBar" { throw NativeSavePanelError.unavailable("unsupported column child") }
        }
        guard (2...32).contains(columns.count) else {
            throw NativeSavePanelError.unavailable("folder column chain unavailable")
        }
        var resources: [NativeSaveDirectoryResource] = []
        for (index, column) in columns.enumerated() {
            guard !columns[..<index].contains(where: { CFEqual($0, column) }) else {
                throw NativeSavePanelError.unavailable("duplicate column identity")
            }
            try parent(column, is: container); identities.append(column)
            var lists: [AXUIElement] = []
            for child in try children(column) {
                let kind = try role(child)
                if kind == "AXList" { lists.append(child) }
                else if kind != "AXScrollBar" { throw NativeSavePanelError.unavailable("unsupported list child") }
            }
            let list = try unique(lists, "column list absent or ambiguous")
            try parent(list, is: column); identities.append(list)
            let selected = try array(list, "AXSelectedChildren")
            if index == columns.count - 1 {
                guard selected.isEmpty else { throw NativeSavePanelError.unavailable("terminal selection present") }
                continue
            }
            let row = try unique(selected, "selected folder absent or ambiguous")
            guard try role(row) == "AXGroup", (try children(list)).filter({ CFEqual($0, row) }).count == 1 else {
                throw NativeSavePanelError.unavailable("selected folder is not a direct row")
            }
            try parent(row, is: list); identities.append(row)
            var fields: [(AXUIElement, AnyObject)] = []
            for child in try children(row) {
                if try role(child) != "AXTextField" { continue }
                if let url = try value(child, "AXURL", optional: true) { fields.append((child, url)) }
            }
            let (field, raw) = try unique(fields, "selected folder URL absent or ambiguous")
            try parent(field, is: row); identities.append(field)
            try charge(); let resource = try directoryResource(raw); try charge(0)
            guard resource.path.hasPrefix("/"), !resource.path.hasPrefix("//"),
                  !resource.path.contains("\\"),
                  !resource.path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
                throw NativeSavePanelError.unavailable("invalid directory resource path")
            }
            if let previous = resources.last,
               URL(fileURLWithPath: resource.path).deletingLastPathComponent().path != previous.path {
                throw NativeSavePanelError.unavailable("selected folders are not a direct directory chain")
            }
            resources.append(resource)
        }
        return (identities, resources)
    }
    private func capture(_ window: AXUIElement, requireFormat: Bool) throws -> NativeSavePanelWitness {
        let (directoryIDs, resources) = try directory(window)
        guard let directory = resources.last else { throw NativeSavePanelError.unavailable("directory absent") }
        let sheet = try sheet(window)
        guard CFEqual(sheet, directoryIDs[1]) else { throw NativeSavePanelError.unavailable("sheet changed") }
        let kinds = ["saveAsNameTextField":"AXTextField", "ContentTypesPopup":"AXPopUpButton",
                     "OKButton":"AXButton", "KeepChangesButton":"AXCheckBox"]
        var found: [String: [(AXUIElement, [AXUIElement])]] = [:]
        var stack: [(AXUIElement, Int, [AXUIElement])] = [(sheet, 0, [window, sheet])]
        var seen: [AXUIElement] = []
        while let (node, depth, ancestry) = stack.popLast() {
            try charge()
            guard depth <= 32, !seen.contains(where: { CFEqual($0, node) }) else {
                throw NativeSavePanelError.unavailable("cyclic or excessive settings hierarchy")
            }
            seen.append(node)
            let kind = try role(node)
            if let id = try text(node, "AXIdentifier", optional: true), let expected = kinds[id] {
                guard kind == expected else { throw NativeSavePanelError.unavailable("unsupported setting role") }
                found[id, default: []].append((node, ancestry))
            }
            if ["AXBrowser", "AXList", "AXOutline"].contains(kind) { continue }
            for child in try children(node).reversed() {
                try parent(child, is: node); stack.append((child, depth + 1, ancestry + [child]))
            }
        }
        var ids = directoryIDs
        func control(_ id: String, optional: Bool = false) throws -> AXUIElement? {
            let matches = found[id] ?? []
            if optional && matches.isEmpty { return nil }
            let (node, ancestry) = try unique(matches, id + " absent or ambiguous")
            for (parent, child) in zip(ancestry, ancestry.dropFirst()) { try self.parent(child, is: parent) }
            ids.append(contentsOf: ancestry.dropFirst(2)); return node
        }
        let name = try control("saveAsNameTextField")!
        guard let filename = try text(name, "AXValue"), !filename.isEmpty,
              ![".", ".."].contains(filename), !filename.contains("/"), !filename.contains("\\"),
              !filename.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
            throw NativeSavePanelError.unavailable("invalid save filename")
        }
        let commit = try control("OKButton")!
        guard let enabled = try value(commit, "AXEnabled"), CFGetTypeID(enabled) == CFBooleanGetTypeID(),
              (enabled as? NSNumber)?.boolValue == true else {
            throw NativeSavePanelError.unavailable("save control not explicitly enabled")
        }
        var formatValue: String?
        if let popup = try control("ContentTypesPopup", optional: true) {
            formatValue = try text(popup, "AXValue")
            guard let value = formatValue, !value.isEmpty else { throw NativeSavePanelError.unavailable("invalid format") }
        }
        let format = formatValue == "Rich Text Document" ? "rtf" : nil
        if requireFormat && format == nil { throw NativeSavePanelError.unavailable("explicit RTF format unavailable") }
        var keepChanges: Int?
        if let checkbox = try control("KeepChangesButton", optional: true) {
            guard let raw = try value(checkbox, "AXValue"), let number = raw as? NSNumber,
                  ["c","C","s","S","i","I","l","L","q","Q","B"].contains(String(cString: number.objCType)),
                  number.int64Value == 0 || number.int64Value == 1 else {
                throw NativeSavePanelError.unavailable("invalid original changes setting")
            }
            keepChanges = number.intValue
        }
        try charge(0)
        return NativeSavePanelWitness(filename: filename, format: format, formatValue: formatValue,
            keepChanges: keepChanges, directory: directory, resources: resources, identities: ids, commit: commit)
    }
    private func compare(_ a: NativeSavePanelWitness, _ b: NativeSavePanelWitness) throws {
        guard a.filename == b.filename, a.formatValue == b.formatValue, a.keepChanges == b.keepChanges,
              a.resources == b.resources, a.identities.count == b.identities.count,
              zip(a.identities, b.identities).allSatisfy({ CFEqual($0, $1) }), CFEqual(a.commit, b.commit) else {
            throw NativeSavePanelError.unavailable("save settings or native identity changed")
        }
    }
    func read(_ window: AXUIElement, requireFormat: Bool = false) throws -> NativeSavePanelWitness {
        remaining = maxNodes; deadline = ProcessInfo.processInfo.systemUptime + maxSeconds
        let before = try capture(window, requireFormat: requireFormat)
        try compare(before, capture(window, requireFormat: requireFormat)); try charge(0)
        return before
    }
    func bindCommit(_ witness: NativeSavePanelWitness, elements: [AXUIElement]) throws -> Int {
        try charge(0)
        guard elements.count <= maxNodes else { throw NativeSavePanelError.unavailable("observation map budget exceeded") }
        var matches: [Int] = []
        for index in elements.indices {
            try charge(0)
            if CFEqual(elements[index], witness.commit) { matches.append(index) }
        }
        return try unique(matches, "save control absent or ambiguous in observation")
    }
    func revalidate(_ window: AXUIElement, witness: NativeSavePanelWitness, requireFormat: Bool = false) throws {
        remaining = maxNodes; deadline = ProcessInfo.processInfo.systemUptime + maxSeconds
        try compare(witness, capture(window, requireFormat: requireFormat)); try charge(0)
    }
    func validateDocument(_ window: AXUIElement, body: AXUIElement) throws {
        remaining = maxNodes; deadline = ProcessInfo.processInfo.systemUptime + maxSeconds
        guard try role(window) == "AXWindow" else {
            throw NativeSavePanelError.unavailable("document window role changed")
        }
        var stack: [(AXUIElement, Int)] = [(window, 0)]
        var seen: [AXUIElement] = []
        var bodies: [AXUIElement] = []
        while let (node, depth) = stack.popLast() {
            try charge()
            guard depth <= 64, !seen.contains(where: { CFEqual($0, node) }) else {
                throw NativeSavePanelError.unavailable("invalid document hierarchy")
            }
            seen.append(node)
            let kind = try role(node)
            // A Save sheet is not the source document's text view. Do not
            // enumerate its file list when validating the source document.
            if kind == "AXSheet" { continue }
            if kind == "AXTextArea", try text(node, "AXIdentifier") == "First Text View" {
                bodies.append(node)
            }
            let children = try children(node)
            for child in children.reversed() {
                try parent(child, is: node)
                stack.append((child, depth + 1))
            }
        }
        guard bodies.count == 1, CFEqual(bodies[0], body) else {
            throw NativeSavePanelError.unavailable("pinned document body absent, replaced or ambiguous")
        }
        try charge(0)
    }
}
