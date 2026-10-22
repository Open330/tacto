import AppKit
import Darwin

private func activityInteger(_ value: Any?) -> Int? {
    guard let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
          !["d","f"].contains(String(cString:n.objCType)), Double(n.intValue) == n.doubleValue else { return nil }
    return n.intValue
}

struct TactoPreferences {
    var loggingEnabled = true
    var retentionDays = 30
    var showControlBanner = true
    var keepHelperWarm = false
    var helperIdleMinutes = 2
    var object: [String: Any] {
        ["schema": 1, "logging_enabled": loggingEnabled, "retention_days": retentionDays,
         "show_control_banner": showControlBanner, "keep_helper_warm": keepHelperWarm,
         "helper_idle_minutes": helperIdleMinutes]
    }
    init(_ object: [String: Any] = [:]) {
        func boolean(_ key: String, _ fallback: Bool) -> Bool {
            guard let n = object[key] as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else { return fallback }
            return n.boolValue
        }
        loggingEnabled = boolean("logging_enabled", true)
        showControlBanner = boolean("show_control_banner", true)
        keepHelperWarm = boolean("keep_helper_warm", false)
        if let n = activityInteger(object["helper_idle_minutes"]), (1...10).contains(n) { helperIdleMinutes = n }
        if let n = activityInteger(object["retention_days"]), [7,30,90].contains(n) { retentionDays = n }
    }
}

struct ActivityStep: Equatable {
    var operation: String
    var action: String
    var target: String
    var elapsed: Double
    var outcome = "완료 확인 없음"
    var error = ""
    var ended = false
    var title: String {
        let names = ["observe":"앱 상태 확인", "capture":"앱 화면 확인", "ask":"모델에 판단 요청",
                     "list_targets":"실행 중인 앱 확인", "document_text":"문서 내용 확인", "menu_state":"메뉴 상태 확인"]
        if operation != "act" { return names[operation] ?? "앱 정보 확인" }
        return ["set_value":"텍스트 입력", "type_text":"텍스트 입력", "menu":"메뉴 실행", "click":"버튼 클릭",
                "double_click":"두 번 클릭", "drag":"드래그", "scroll":"스크롤", "press_key":"키 입력",
                "prepare_window":"작업 창 선택", "reopen_window":"창 다시 열기", "upload":"파일 첨부",
                "select_option":"항목 선택", "read":"결과 확인"][action] ?? "앱 동작 실행"
    }
}

struct ActivityRecord: Equatable {
    let url: URL
    var command = ""
    var target = ""
    var started = Date()
    var elapsed: Double = 0
    var exitCode: Int?
    var steps: [ActivityStep] = []
    var error = ""
    var truncated = false
    var damaged = false
    var foreground: Bool?
    var presentationSkipped = false
    var modelCalls: Int?
    var complete: Bool { exitCode != nil && !command.isEmpty }
    var failed: Bool { exitCode.map { $0 != 0 } ?? false }
    var status: String { !complete ? "완료 확인 없음" : failed ? "실패" : "완료" }
    var title: String {
        ["first-run":"첫 작업", "observe":"앱 관찰", "run":"워크플로 실행", "replay":"워크플로 재사용",
         "do":"에이전트 작업", "session":"연속 작업", "batch":"반복 실행", "doctor":"상태 확인",
         "apps":"앱 목록 확인", "launch":"앱 열기", "capture":"화면 확인", "drag":"드래그",
         "web-upload":"파일 첨부"][command] ?? "Tacto 실행"
    }
    var appName: String {
        ["com.apple.TextEdit":"TextEdit", "com.apple.finder":"Finder", "com.apple.calculator":"계산기",
         "com.apple.Safari":"Safari", "com.google.Chrome":"Chrome"][target] ?? (target.isEmpty ? "이 Mac" : target)
    }
}

final class ActivityStore {
    let root: URL
    var logs: URL { root.appendingPathComponent("Logs") }
    var preferencesURL: URL { root.appendingPathComponent("preferences.json") }
    private let pattern = try! NSRegularExpression(pattern: "^tacto-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\\.jsonl$")
    private var cache: [URL: (Date, Int, ActivityRecord)] = [:]
    let maximumBytes = 1_048_576 + 8192
    init(root: URL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Tacto")) { self.root = root }
    func ownedDirectory(_ url: URL, create: Bool = false) throws {
        if create { try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions:0o700]) }
        let a = try FileManager.default.attributesOfItem(atPath: url.path)
        guard a[.type] as? FileAttributeType == .typeDirectory, (a[.ownerAccountID] as? NSNumber)?.uint32Value == getuid() else {
            throw NSError(domain:"TactoActivity", code:1)
        }
    }
    func readPrivate(_ url: URL, limit: Int) throws -> Data {
        let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else { throw NSError(domain:NSPOSIXErrorDomain, code:Int(errno)) }
        let handle = FileHandle(fileDescriptor:fd, closeOnDealloc:true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(), info.st_size <= limit else {
            throw NSError(domain:"TactoActivity", code:2)
        }
        return try handle.read(upToCount:limit) ?? Data()
    }
    func preferences() throws -> TactoPreferences {
        guard FileManager.default.fileExists(atPath: preferencesURL.path) else { return TactoPreferences() }
        try ownedDirectory(root)
        guard let object = try JSONSerialization.jsonObject(with: readPrivate(preferencesURL, limit:16384)) as? [String:Any],
              (object["schema"] == nil || activityInteger(object["schema"]) == 1) else { throw NSError(domain:"TactoActivity", code:3) }
        return TactoPreferences(object)
    }
    func save(_ settings: TactoPreferences) throws {
        try ownedDirectory(root, create:true)
        if let a = try? FileManager.default.attributesOfItem(atPath:preferencesURL.path) {
            guard a[.type] as? FileAttributeType == .typeRegular, (a[.ownerAccountID] as? NSNumber)?.uint32Value == getuid() else {
                throw NSError(domain:"TactoActivity", code:4)
            }
        }
        let temporary = root.appendingPathComponent(".preferences-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:temporary) }
        let fd = Darwin.open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw NSError(domain:NSPOSIXErrorDomain, code:Int(errno)) }
        let handle = FileHandle(fileDescriptor:fd, closeOnDealloc:true)
        do {
            try handle.write(contentsOf:JSONSerialization.data(withJSONObject:settings.object, options:[.sortedKeys]))
            try handle.synchronize(); try handle.close()
        } catch { try? handle.close(); throw error }
        guard Darwin.rename(temporary.path, preferencesURL.path) == 0 else { throw NSError(domain:NSPOSIXErrorDomain, code:Int(errno)) }
    }
    func files() throws -> [(URL, Date, Int)] {
        guard FileManager.default.fileExists(atPath:logs.path) else { return [] }
        try ownedDirectory(root); try ownedDirectory(logs)
        return try FileManager.default.contentsOfDirectory(at:logs, includingPropertiesForKeys:nil).compactMap { url in
            let name = url.lastPathComponent
            guard pattern.firstMatch(in:name, range:NSRange(name.startIndex..., in:name)) != nil,
                  let a = try? FileManager.default.attributesOfItem(atPath:url.path),
                  a[.type] as? FileAttributeType == .typeRegular, (a[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
                  let size = a[.size] as? Int, size <= maximumBytes else { return nil }
            return (url, a[.modificationDate] as? Date ?? .distantPast, size)
        }.sorted { $0.1 > $1.1 }
    }
    func records() throws -> [ActivityRecord] {
        let candidates = Array(try files().prefix(200))
        let keep = Set(candidates.map { $0.0 }); cache = cache.filter { keep.contains($0.key) }
        return candidates.compactMap { url, date, size in
            if let c = cache[url], c.0 == date && c.1 == size { return c.2 }
            guard let data = try? readPrivate(url, limit:maximumBytes) else { return nil }
            let record = parse(data, url:url, modified:date)
            cache[url] = (date, size, record); return record
        }
    }
    func parse(_ data: Data, url: URL, modified: Date) -> ActivityRecord {
        var record = ActivityRecord(url:url); record.started = modified
        guard let text = String(data:data, encoding:.utf8) else { record.damaged = true; return record }
        let lines = text.split(separator:"\n", omittingEmptySubsequences:true)
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        for (index, line) in lines.enumerated() {
            guard let row = try? JSONSerialization.jsonObject(with:Data(line.utf8)) as? [String:Any],
                  let event = row["event"] as? String, activityInteger(row["schema"]) == 1 else {
                if index < lines.count - 1 || text.hasSuffix("\n") { record.damaged = true }; continue
            }
            let elapsed = (row["elapsed_ms"] as? NSNumber)?.doubleValue ?? 0
            if elapsed.isFinite && elapsed >= 0 && elapsed < 1e12 { record.elapsed = max(record.elapsed, elapsed) }
            if let target = row["target"] as? String { record.target = String(target.prefix(255)) }
            switch event {
            case "command_start":
                record.command = row["command"] as? String ?? ""
                if let date = row["at"] as? String, let parsed = formatter.date(from:date) { record.started = parsed }
            case "command_end": record.exitCode = activityInteger(row["exit_code"])
            case "log_truncated": record.truncated = true
            case "task_result":
                record.error = row["error"] as? String ?? ""
                record.foreground = row["foreground_verified"] as? Bool
                record.presentationSkipped = row["presentation_skipped"] as? Bool ?? false
                record.modelCalls = activityInteger(row["model_calls"])
            case "access_start":
                record.steps.append(ActivityStep(operation:row["operation"] as? String ?? "", action:row["action"] as? String ?? "",
                                                 target:row["target"] as? String ?? "", elapsed:record.elapsed))
            case "access_end":
                if let i = record.steps.firstIndex(where: { !$0.ended && $0.operation == row["operation"] as? String && $0.action == (row["action"] as? String ?? "") }) {
                    record.steps[i].ended = true
                    record.steps[i].error = row["error"] as? String ?? ""
                    record.steps[i].outcome = row["type"] as? String == "error" || row["outcome"] as? String == "exception" ? "실패" : "응답 확인"
                }
            default: break
            }
        }
        return record
    }
    func deleteRecords() throws {
        for (url, _, _) in try files() { try FileManager.default.removeItem(at:url) }
        cache.removeAll()
    }
}

private let activityCanvas = NSColor(name:nil) { a in
    a.bestMatch(from:[.aqua,.darkAqua]) == .darkAqua ? NSColor(srgbRed:0.10,green:0.11,blue:0.105,alpha:1) : NSColor(srgbRed:0.975,green:0.973,blue:0.96,alpha:1)
}
private let activitySidebar = NSColor(name:nil) { a in
    a.bestMatch(from:[.aqua,.darkAqua]) == .darkAqua ? NSColor(srgbRed:0.13,green:0.14,blue:0.13,alpha:1) : NSColor(srgbRed:0.94,green:0.94,blue:0.925,alpha:1)
}

private final class ActivityBackground: NSView {
    var fill: NSColor = activityCanvas
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() { effectiveAppearance.performAsCurrentDrawingAppearance { layer?.backgroundColor = fill.cgColor } }
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); needsDisplay = true }
}
private final class ActivityCard: NSStackView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() { effectiveAppearance.performAsCurrentDrawingAppearance { layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor } }
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); needsDisplay = true }
}
private final class ActivityNavigationButton: NSButton {
    override func draw(_ dirtyRect: NSRect) {
        let color: NSColor = state == .on ? .controlAccentColor : .labelColor
        if state == .on || isHighlighted {
            NSColor.controlAccentColor.withAlphaComponent(state == .on ? 0.12 : 0.06).setFill()
            NSBezierPath(roundedRect:bounds,xRadius:8,yRadius:8).fill()
        }
        let symbol = image?.withSymbolConfiguration(NSImage.SymbolConfiguration(paletteColors:[color])) ?? image
        symbol?.draw(in:NSRect(x:12,y:(bounds.height-16)/2,width:16,height:16),from:.zero,operation:.sourceOver,fraction:1,respectFlipped:true,hints:nil)
        let paragraph = NSMutableParagraphStyle(); paragraph.lineBreakMode = .byTruncatingTail
        (title as NSString).draw(in:NSRect(x:36,y:(bounds.height-17)/2,width:bounds.width-48,height:18),withAttributes:[.font:NSFont.systemFont(ofSize:13,weight:.medium),.foregroundColor:color,.paragraphStyle:paragraph])
    }
}

private final class ActivityDocumentStack: NSStackView { override var isFlipped: Bool { true } }

private func activityErrorMessage(_ code: String) -> String {
    ["new_window_not_unique":"새 문서 창을 하나로 확인하지 못했어요.",
     "helper_unavailable":"앱 조작 연결을 사용할 수 없어요. Mac 권한·에이전트 설정을 확인하세요.",
     "target_not_found":"대상 앱을 찾지 못했어요.", "no_window_content":"아직 열린 창이 없어요.",
     "observation_unavailable":"앱 상태를 읽지 못했어요.", "aborted_by_user":"사용자가 작업을 중단했어요.",
     "new_document_not_empty":"새 문서가 비어 있지 않아 입력을 멈췄어요.",
     "application_identity_changed":"대상 앱이 바뀌어 작업을 멈췄어요.",
     "text_not_verified":"입력 결과를 확인하지 못했어요.",
     "action_failed":"요청한 동작을 완료하지 못했어요."][code] ?? ("오류 코드: " + String(code.prefix(120)))
}

private func activityLabel(_ text: String, _ size: CGFloat = 13, _ weight: NSFont.Weight = .regular, secondary: Bool = false) -> NSTextField {
    let field = NSTextField(wrappingLabelWithString:text)
    field.font = .systemFont(ofSize:size, weight:weight)
    field.textColor = secondary ? .secondaryLabelColor : .labelColor
    field.setContentCompressionResistancePriority(.required, for:.vertical)
    field.setContentCompressionResistancePriority(.defaultLow, for:.horizontal)
    return field
}

final class ActivityCenter: NSWindowController, NSWindowDelegate, NSTableViewDataSource, NSTableViewDelegate, NSSearchFieldDelegate, NSSplitViewDelegate {
    let store: ActivityStore
    var onSetup: (() -> Void)?
    private let queue = DispatchQueue(label:"ai.callabo.tacto.activity-reader", qos:.userInitiated)
    private let table = NSTableView()
    private let search = NSSearchField()
    private let filter = NSSegmentedControl(labels:["전체", "실패", "완료 확인 없음"], trackingMode:.selectOne, target:nil, action:nil)
    private let heading = activityLabel("실행 기록", 24, .semibold)
    private let summary = activityLabel("불러오는 중…", 12, secondary:true)
    private let detail = ActivityDocumentStack()
    private let history = NSStackView()
    private let settings = ActivityDocumentStack()
    private let content = NSView()
    private let split = NSSplitView()
    private let status = activityLabel("상태 확인 중", 11, secondary:true)
    private let saveStatus = activityLabel("변경 사항은 다음 작업부터 적용됩니다.", 12, secondary:true)
    private let logToggle = NSButton(checkboxWithTitle:"실행 기록 자동 저장", target:nil, action:nil)
    private let bannerToggle = NSButton(checkboxWithTitle:"작업 중 배너 표시", target:nil, action:nil)
    private let warmToggle = NSButton(checkboxWithTitle:"빠른 실행(helper 유지)", target:nil, action:nil)
    private let helperIdle = NSPopUpButton(frame:.zero,pullsDown:false)
    private let retention = NSPopUpButton(frame:.zero,pullsDown:false)
    private let historyButton = ActivityNavigationButton()
    private let settingsButton = ActivityNavigationButton()
    private var allRecords: [ActivityRecord] = []
    private var filtered: [ActivityRecord] = []
    private var selected: URL?
    private var timer: Timer?
    private var loading = false
    private let refreshAutomatically: Bool
    private var isSettings = false
    private var currentSettings = TactoPreferences()
    private var rendered: ActivityRecord?
    private let dateFormatter: DateFormatter = { let f = DateFormatter(); f.dateStyle = .medium; f.timeStyle = .short; return f }()

    init(store: ActivityStore = ActivityStore(), refreshAutomatically: Bool = true) {
        self.store = store
        self.refreshAutomatically = refreshAutomatically
        let window = NSWindow(contentRect:NSRect(x:0,y:0,width:1060,height:700), styleMask:[.titled,.closable,.miniaturizable,.resizable], backing:.buffered, defer:false)
        super.init(window:window)
        window.title = "Tacto"; window.titlebarAppearsTransparent = true; window.isReleasedWhenClosed = false
        window.contentMinSize = NSSize(width:900,height:600); window.delegate = self
        window.backgroundColor = .windowBackgroundColor
        let background = ActivityBackground(frame:window.contentView!.bounds); background.wantsLayer = true; window.contentView = background
        if let visible = NSScreen.main?.visibleFrame { window.setContentSize(NSSize(width:max(900,min(1060,visible.width-32)),height:max(600,min(700,visible.height-48)))) }
        build(); window.contentView?.layoutSubtreeIfNeeded(); split.setPosition(300,ofDividerAt:0); window.center()
    }
    required init?(coder:NSCoder) { nil }
    private func column(_ views: [NSView], spacing: CGFloat = 12) -> NSStackView {
        let stack = NSStackView(views:views); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = spacing
        return stack
    }
    private func button(_ title: String, _ icon: String, _ selector: Selector) -> NSButton {
        let b = NSButton(title:title, image:NSImage(systemSymbolName:icon, accessibilityDescription:title) ?? NSImage(), target:self, action:selector)
        b.bezelStyle = .rounded; b.imagePosition = .imageLeading; b.alignment = .left
        return b
    }
    private func build() {
        guard let root = window?.contentView else { return }
        let sidebar = ActivityBackground(); sidebar.fill = activitySidebar; sidebar.wantsLayer = true
        let brand = activityLabel("Tacto", 23, .bold)
        let logo = NSImageView(); logo.image = NSImage(contentsOf:Bundle.main.url(forResource:"tacto-app", withExtension:"icns") ?? URL(fileURLWithPath:"/nonexistent"))
        logo.imageScaling = .scaleProportionallyUpOrDown; logo.widthAnchor.constraint(equalToConstant:32).isActive = true; logo.heightAnchor.constraint(equalToConstant:32).isActive = true
        let brandRow = NSStackView(views:[logo,brand]); brandRow.spacing = 8
        historyButton.title = "실행 기록"; historyButton.image = NSImage(systemSymbolName:"clock.arrow.circlepath", accessibilityDescription:nil)
        settingsButton.title = "설정"; settingsButton.image = NSImage(systemSymbolName:"slider.horizontal.3", accessibilityDescription:nil)
        for b in [historyButton,settingsButton] { b.font = .systemFont(ofSize:13,weight:.medium); b.target = self; b.action = #selector(navigate(_:)); b.bezelStyle = .rounded; b.isBordered = false; b.setButtonType(.pushOnPushOff); b.wantsLayer = true; b.layer?.cornerRadius = 8; b.imagePosition = .imageLeading; b.alignment = .left; b.widthAnchor.constraint(equalToConstant:176).isActive = true; b.heightAnchor.constraint(equalToConstant:38).isActive = true }
        historyButton.setAccessibilityIdentifier("tacto.activity.history"); settingsButton.setAccessibilityIdentifier("tacto.activity.settings")
        let setup = button("Mac 권한·에이전트", "arrow.up.forward.app", #selector(openSetup))
        let tagline = activityLabel("에이전트의 작업 공간",11,secondary:true)
        let spacer = NSView(); spacer.setContentHuggingPriority(.init(1),for:.vertical)
        let side = column([brandRow,tagline,historyButton,settingsButton,spacer,status,setup])
        side.spacing = 8; side.setCustomSpacing(24,after:tagline)
        status.preferredMaxLayoutWidth = 176
        status.widthAnchor.constraint(lessThanOrEqualToConstant:176).isActive = true
        sidebar.addSubview(side); side.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([side.topAnchor.constraint(equalTo:sidebar.topAnchor,constant:26),side.leadingAnchor.constraint(equalTo:sidebar.leadingAnchor,constant:16),side.trailingAnchor.constraint(equalTo:sidebar.trailingAnchor,constant:-16),side.bottomAnchor.constraint(equalTo:sidebar.bottomAnchor,constant:-20)])
        root.addSubview(sidebar); root.addSubview(content)
        for v in [sidebar,content] { v.translatesAutoresizingMaskIntoConstraints = false }
        NSLayoutConstraint.activate([sidebar.leadingAnchor.constraint(equalTo:root.leadingAnchor),sidebar.topAnchor.constraint(equalTo:root.topAnchor),sidebar.bottomAnchor.constraint(equalTo:root.bottomAnchor),sidebar.widthAnchor.constraint(equalToConstant:208),content.leadingAnchor.constraint(equalTo:sidebar.trailingAnchor),content.trailingAnchor.constraint(equalTo:root.trailingAnchor),content.topAnchor.constraint(equalTo:root.topAnchor),content.bottomAnchor.constraint(equalTo:root.bottomAnchor)])
        buildHistory(); buildSettings(); navigate(historyButton)
    }
    private func pin(_ view: NSView, into container: NSView, inset: CGFloat = 24) {
        container.addSubview(view); view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([view.leadingAnchor.constraint(equalTo:container.leadingAnchor,constant:inset),view.trailingAnchor.constraint(equalTo:container.trailingAnchor,constant:-inset),view.topAnchor.constraint(equalTo:container.topAnchor,constant:inset),view.bottomAnchor.constraint(equalTo:container.bottomAnchor,constant:-inset)])
    }
    private func buildHistory() {
        history.orientation = .vertical; history.alignment = .leading; history.spacing = 16
        let refresh = button("새로고침", "arrow.clockwise", #selector(refresh))
        let top = NSStackView(views:[heading,NSView(),refresh]); top.alignment = .centerY
        search.placeholderString = "앱 또는 작업 검색"; search.delegate = self; search.setAccessibilityIdentifier("tacto.activity.search")
        search.widthAnchor.constraint(greaterThanOrEqualToConstant:160).isActive = true
        search.heightAnchor.constraint(greaterThanOrEqualToConstant:28).isActive = true
        filter.target = self; filter.action = #selector(applyFilter); filter.selectedSegment = 0
        let tools = NSStackView(views:[search,filter]); tools.spacing = 12
        let list = NSScrollView(); list.hasVerticalScroller = true; list.autohidesScrollers = true
        let col = NSTableColumn(identifier:NSUserInterfaceItemIdentifier("session")); table.addTableColumn(col)
        table.headerView = nil; table.rowHeight = 64; table.intercellSpacing = .zero
        table.style = .fullWidth; table.delegate = self; table.dataSource = self; table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
        table.setAccessibilityIdentifier("tacto.activity.sessions"); list.documentView = table
        let details = NSScrollView(); details.hasVerticalScroller = true; details.autohidesScrollers = true
        detail.orientation = .vertical; detail.alignment = .leading; detail.spacing = 10
        detail.edgeInsets = NSEdgeInsets(top:24,left:24,bottom:24,right:24); detail.translatesAutoresizingMaskIntoConstraints = false
        details.documentView = detail
        detail.widthAnchor.constraint(equalTo:details.contentView.widthAnchor).isActive = true
        split.isVertical = true; split.dividerStyle = .thin; split.delegate = self
        split.wantsLayer = true; split.layer?.cornerRadius = 10; split.layer?.masksToBounds = true
        split.addArrangedSubview(list); split.addArrangedSubview(details)
        split.setHoldingPriority(.defaultHigh,forSubviewAt:0)
        split.setHoldingPriority(.defaultLow,forSubviewAt:1)
        list.widthAnchor.constraint(greaterThanOrEqualToConstant:240).isActive = true
        details.widthAnchor.constraint(greaterThanOrEqualToConstant:300).isActive = true
        for view in [top,summary,tools,split] { history.addArrangedSubview(view) }
        for v in [top,tools,split] { v.widthAnchor.constraint(equalTo:history.widthAnchor).isActive = true }
        split.heightAnchor.constraint(greaterThanOrEqualToConstant:360).isActive = true
        pin(history,into:content)
    }
    private func settingsCard(_ title: String, _ explanation: String, _ control: NSView) -> NSView {
        let stack = ActivityCard(views:[activityLabel(title,16,.semibold),activityLabel(explanation,12,secondary:true),control]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top:20,left:20,bottom:20,right:20)
        for view in stack.arrangedSubviews where view is NSTextField {
            view.widthAnchor.constraint(equalTo:stack.widthAnchor,constant:-40).isActive = true
        }
        stack.wantsLayer = true; stack.layer?.cornerRadius = 12
        return stack
    }
    private func buildSettings() {
        settings.orientation = .vertical; settings.alignment = .leading; settings.spacing = 14
        logToggle.target = self; logToggle.action = #selector(saveSettings)
        bannerToggle.target = self; bannerToggle.action = #selector(saveSettings)
        bannerToggle.setAccessibilityIdentifier("tacto.settings.control-banner")
        logToggle.setAccessibilityIdentifier("tacto.settings.logging")
        retention.addItems(withTitles:["7일", "30일", "90일"]); retention.target = self; retention.action = #selector(saveSettings)
        retention.setAccessibilityIdentifier("tacto.settings.retention")
        warmToggle.target = self; warmToggle.action = #selector(saveSettings)
        warmToggle.setAccessibilityIdentifier("tacto.settings.keep-helper-warm")
        helperIdle.addItems(withTitles:(1...10).map { "\($0)분" })
        helperIdle.target = self; helperIdle.action = #selector(saveSettings)
        helperIdle.setAccessibilityIdentifier("tacto.settings.helper-idle")
        let warmControls = NSStackView(views:[warmToggle,activityLabel("사용하지 않을 때 종료",12,secondary:true),helperIdle])
        warmControls.orientation = .vertical; warmControls.alignment = .leading; warmControls.spacing = 10
        let remove = button("저장된 기록 모두 삭제…", "trash", #selector(confirmDelete))
        let cards = [settingsCard("빠른 실행", "도우미를 잠시 유지해 다음 작업을 더 빠르게 시작합니다. 기본은 꺼짐입니다. 유휴 시간이 지나면 자동 종료하며, 환경변수로 지정한 실행 방식이 우선합니다.",warmControls),
                     settingsCard("실행 기록", "접근한 앱·동작·결과·시간을 이 Mac에 저장합니다. 화면 내용·입력값·프롬프트는 기록하지 않습니다.",logToggle),
                     settingsCard("기록 보관 기간", "선택한 기간과 최대 200개·20MB 제한 안에서 다음 작업 시작 시 정리합니다.",retention),
                     settingsCard("작업 표시", "끄면 우상단 배너를 숨깁니다. 기존 Tacto 메뉴에서 작업 상태를 확인하거나 중단할 수 있고, Esc로도 중단할 수 있습니다.",bannerToggle)]
        settings.addArrangedSubview(activityLabel("설정",24,.semibold))
        for card in cards { settings.addArrangedSubview(card); card.widthAnchor.constraint(equalTo:settings.widthAnchor).isActive = true }
        for v in [saveStatus,remove,activityLabel("기록은 Tacto를 통한 접근 내역입니다. 에이전트의 내부 추론이나 전체 대화는 포함하지 않습니다.",12,secondary:true)] { settings.addArrangedSubview(v) }
        settings.addArrangedSubview(activityLabel("Tacto · " + (Bundle.main.object(forInfoDictionaryKey:"TactoReleaseTag") as? String ?? "개발 버전"),11,secondary:true))
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true; scroll.drawsBackground = false; scroll.contentView.drawsBackground = false
        settings.translatesAutoresizingMaskIntoConstraints = false; scroll.documentView = settings
        settings.widthAnchor.constraint(equalTo:scroll.contentView.widthAnchor).isActive = true
        pin(scroll,into:content); scroll.isHidden = true; scroll.identifier = NSUserInterfaceItemIdentifier("settings-page")
    }
    @objc private func navigate(_ sender: NSButton) {
        isSettings = sender === settingsButton
        history.isHidden = isSettings
        content.subviews.first(where: { $0.identifier?.rawValue == "settings-page" })?.isHidden = !isSettings
        historyButton.state = isSettings ? .off : .on; settingsButton.state = isSettings ? .on : .off
        for b in [historyButton,settingsButton] { b.contentTintColor = b.state == .on ? .controlAccentColor : .labelColor; b.needsDisplay = true }
        if isSettings { loadSettings() } else if refreshAutomatically { refresh() }
    }
    func present(settings: Bool = false) {
        navigate(settings ? settingsButton : historyButton)
        showWindow(nil); window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
        if timer == nil { timer = Timer.scheduledTimer(withTimeInterval:2,repeats:true) { [weak self] _ in if self?.window?.isVisible == true && self?.window?.isMiniaturized == false && NSApp.isActive && self?.isSettings == false { self?.refresh() } } }
    }
    func presentCurrent() { present(settings:isSettings) }
    func updateStatus(_ text: String) { status.stringValue = text }
    @objc private func openSetup() { onSetup?() }
    @objc func refresh() {
        guard !loading else { return }; loading = true
        queue.async { [weak self] in
            guard let self else { return }
            let result = Result { (try self.store.records(), (try? self.store.preferences()) ?? TactoPreferences()) }
            DispatchQueue.main.async {
                self.loading = false
                switch result {
                case .success(let value): self.allRecords = value.0; self.currentSettings = value.1; self.applyFilter()
                case .failure: self.summary.stringValue = "실행 기록을 읽지 못했어요. 잠시 후 새로고침해 주세요."
                }
            }
        }
    }
    @objc private func applyFilter() {
        let query = search.stringValue.trimmingCharacters(in:.whitespacesAndNewlines)
        filtered = allRecords.filter { r in
            (filter.selectedSegment != 1 || r.failed) && (filter.selectedSegment != 2 || !r.complete)
            && (query.isEmpty || "\(r.title) \(r.appName) \(r.status)".localizedCaseInsensitiveContains(query))
        }
        let failed = allRecords.filter { $0.failed }.count
        summary.stringValue = "최근 \(allRecords.count)개 실행 · 실패 \(failed)개 · \(filtered.count)개 표시" + (currentSettings.loggingEnabled ? "" : " · 새 기록 저장 꺼짐")
        table.reloadData()
        if let id = selected, let i = filtered.firstIndex(where: { $0.url == id }) { table.selectRowIndexes(IndexSet(integer:i),byExtendingSelection:false); showDetail(filtered[i]) }
        else if let first = filtered.first { selected = first.url; table.selectRowIndexes(IndexSet(integer:0),byExtendingSelection:false); showDetail(first) }
        else { selected = nil; showDetail(nil) }
    }
    func controlTextDidChange(_ obj: Notification) { applyFilter() }
    func numberOfRows(in tableView: NSTableView) -> Int { filtered.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard filtered.indices.contains(row) else { return nil }
        let r = filtered[row]
        let cell = NSTableCellView()
        let title = activityLabel(r.title,13,.semibold)
        let subtitle = activityLabel("\(r.appName) · \(dateFormatter.string(from:r.started))",11,secondary:true)
        let mark = activityLabel(r.status,11,.semibold)
        mark.textColor = r.failed ? .systemRed : r.complete ? .systemGreen : .secondaryLabelColor
        title.maximumNumberOfLines = 1; subtitle.maximumNumberOfLines = 1
        title.lineBreakMode = .byTruncatingTail; subtitle.lineBreakMode = .byTruncatingTail
        for v in [title,subtitle,mark] { cell.addSubview(v); v.translatesAutoresizingMaskIntoConstraints = false }
        cell.textField = title
        NSLayoutConstraint.activate([title.leadingAnchor.constraint(equalTo:cell.leadingAnchor,constant:12),title.topAnchor.constraint(equalTo:cell.topAnchor,constant:13),title.trailingAnchor.constraint(lessThanOrEqualTo:mark.leadingAnchor,constant:-8),mark.trailingAnchor.constraint(equalTo:cell.trailingAnchor,constant:-12),mark.topAnchor.constraint(equalTo:title.topAnchor),subtitle.leadingAnchor.constraint(equalTo:title.leadingAnchor),subtitle.trailingAnchor.constraint(equalTo:cell.trailingAnchor,constant:-12),subtitle.topAnchor.constraint(equalTo:title.bottomAnchor,constant:5)])

        return cell
    }
    func tableViewSelectionDidChange(_ notification: Notification) {
        guard filtered.indices.contains(table.selectedRow) else { return }
        let record = filtered[table.selectedRow]; selected = record.url; showDetail(record)
    }
    private func showDetail(_ r: ActivityRecord?) {
        if let r, rendered == r { return }
        rendered = r
        for v in detail.arrangedSubviews { detail.removeArrangedSubview(v); v.removeFromSuperview() }
        guard let r else {
            detail.addArrangedSubview(activityLabel(allRecords.isEmpty ? (currentSettings.loggingEnabled ? "아직 실행 기록이 없어요" : "실행 기록 저장이 꺼져 있어요") : "조건에 맞는 기록이 없어요",20,.semibold))
            detail.addArrangedSubview(activityLabel(currentSettings.loggingEnabled ? "Tacto로 작업하면 접근 내역과 결과가 여기에 표시됩니다." : "설정에서 켜면 다음 작업부터 기록을 남깁니다.",13,secondary:true)); layoutDetail(); return
        }
        detail.addArrangedSubview(activityLabel(r.title,20,.semibold))
        detail.addArrangedSubview(activityLabel("\(r.appName) · \(r.status)",13,.medium))
        detail.addArrangedSubview(activityLabel(dateFormatter.string(from:r.started),12,secondary:true))
        var metrics = String(format:"%.2f초 · 동작 %d회",r.elapsed/1000,r.steps.filter { $0.operation == "act" }.count)
        if let calls = r.modelCalls { metrics += " · 모델 호출 \(calls)회" }
        detail.addArrangedSubview(activityLabel(metrics,12,secondary:true))
        if r.foreground == true { detail.addArrangedSubview(activityLabel("결과 문서를 앞으로 표시했어요.",12,.medium)) }
        if r.foreground == false { detail.addArrangedSubview(activityLabel(r.presentationSkipped ? "설정에 따라 백그라운드로 완료했어요." : "결과 화면 표시를 확인하지 못했어요.",12,secondary:true)) }
        if !r.error.isEmpty { detail.addArrangedSubview(activityLabel(activityErrorMessage(r.error),12)) }
        if r.truncated || r.damaged { detail.addArrangedSubview(activityLabel(r.truncated ? "용량 제한으로 일부 접근 기록이 생략됐습니다." : "일부 기록이 손상되어 읽을 수 없습니다.",12,secondary:true)) }
        let divider = NSBox(); divider.boxType = .separator; detail.addArrangedSubview(divider)
        if r.steps.isEmpty { detail.addArrangedSubview(activityLabel("세부 접근 내역이 없는 실행입니다.",12,secondary:true)) }
        for step in r.steps {
            let icon = NSImageView(image:NSImage(systemSymbolName:step.outcome == "실패" ? "exclamationmark.circle" : step.operation == "observe" ? "eye" : "cursorarrow",accessibilityDescription:nil) ?? NSImage())
            icon.contentTintColor = step.outcome == "실패" ? .systemRed : .secondaryLabelColor
            icon.widthAnchor.constraint(equalToConstant:18).isActive = true
            icon.heightAnchor.constraint(equalToConstant:18).isActive = true
            let texts = column([activityLabel(step.title,13,.medium),activityLabel(String(format:"%.2f초 · %@",step.elapsed/1000,step.outcome),11,secondary:true)],spacing:4)
            if !step.error.isEmpty { texts.addArrangedSubview(activityLabel(activityErrorMessage(step.error),11,secondary:true)) }
            let row = NSStackView(views:[icon,texts]); row.alignment = .top; row.spacing = 12
            detail.addArrangedSubview(row)
        }
        layoutDetail()
    }
    private func layoutDetail() {
        for v in detail.arrangedSubviews { v.widthAnchor.constraint(equalTo:detail.widthAnchor,constant:-48).isActive = true; if let f = v as? NSTextField { f.preferredMaxLayoutWidth = max(200,detail.frame.width-48) } }
        detail.layoutSubtreeIfNeeded(); detail.setFrameSize(NSSize(width:detail.frame.width,height:detail.fittingSize.height))
    }
    private func loadSettings() {
        do { currentSettings = try store.preferences(); saveStatus.stringValue = "변경 사항은 다음 작업부터 적용됩니다." }
        catch { saveStatus.stringValue = "설정을 읽지 못했어요. 기본값을 표시합니다."; currentSettings = TactoPreferences() }
        logToggle.state = currentSettings.loggingEnabled ? .on : .off
        bannerToggle.state = currentSettings.showControlBanner ? .on : .off
        warmToggle.state = currentSettings.keepHelperWarm ? .on : .off
        helperIdle.selectItem(at:currentSettings.helperIdleMinutes-1)
        helperIdle.isEnabled = currentSettings.keepHelperWarm
        retention.selectItem(at:[7,30,90].firstIndex(of:currentSettings.retentionDays) ?? 1)
    }
    @objc private func saveSettings() {
        var desired = currentSettings; desired.showControlBanner = bannerToggle.state == .on
        desired.loggingEnabled = logToggle.state == .on
        desired.keepHelperWarm = warmToggle.state == .on
        desired.helperIdleMinutes = max(1,helperIdle.indexOfSelectedItem+1)
        desired.retentionDays = [7,30,90][max(0,retention.indexOfSelectedItem)]
        do { try store.save(desired); currentSettings = desired; helperIdle.isEnabled = desired.keepHelperWarm; saveStatus.stringValue = "저장했어요. 다음 작업부터 적용됩니다." }
        catch { loadSettings(); saveStatus.stringValue = "설정을 저장하지 못했어요. 이전 설정을 유지합니다." }
    }
    @objc private func confirmDelete() {
        guard let window else { return }
        let alert = NSAlert(); alert.messageText = "저장된 실행 기록을 모두 삭제할까요?"
        alert.informativeText = "이 Mac의 접근 기록이 삭제됩니다. 작업과 저장된 워크플로는 유지됩니다."
        alert.addButton(withTitle:"삭제"); alert.addButton(withTitle:"취소")
        alert.buttons.first?.hasDestructiveAction = true
        alert.beginSheetModal(for:window) { [weak self] response in
            guard response == .alertFirstButtonReturn, let self else { return }
            self.queue.async {
                let result = Result { try self.store.deleteRecords() }
                DispatchQueue.main.async {
                    switch result { case .success: self.saveStatus.stringValue = "저장된 기록을 삭제했어요."
                    case .failure: self.saveStatus.stringValue = "일부 기록을 삭제하지 못했어요." }
                    self.refresh()
                }
            }
        }
    }
    func snapshot(to directory: URL, settingsPage: Bool = false, size: NSSize = NSSize(width:1060,height:700)) throws {
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        allRecords = try store.records(); currentSettings = (try? store.preferences()) ?? TactoPreferences(); applyFilter()
        navigate(settingsPage ? settingsButton : historyButton)
        window?.setContentSize(size)
        guard let view = window?.contentView else { return }
        view.layoutSubtreeIfNeeded()
        table.reloadData(); view.layoutSubtreeIfNeeded()
        if !settingsPage { showDetail(filtered.first) }
        view.layoutSubtreeIfNeeded()
        func redraw(_ v: NSView) { v.needsDisplay = true; v.displayIfNeeded(); for child in v.subviews { redraw(child) } }
        redraw(view)
        if let bitmap = view.bitmapImageRepForCachingDisplay(in:view.bounds) {
            view.cacheDisplay(in:view.bounds,to:bitmap)
            try bitmap.representation(using:.png,properties:[:])?.write(to:directory.appendingPathComponent("app.png"))
        }
        let report: [String:Any] = ["record_count":allRecords.count,"visible_count":filtered.count,"settings_page":settingsPage,"width":view.bounds.width,"height":view.bounds.height]
        try JSONSerialization.data(withJSONObject:report,options:[.sortedKeys]).write(to:directory.appendingPathComponent("layout.json"))
    }
    func splitView(_ splitView: NSSplitView, shouldAdjustSizeOfSubview view: NSView) -> Bool {
        view === splitView.subviews.last
    }
    func splitView(_ splitView: NSSplitView, constrainMinCoordinate proposedMinimumPosition: CGFloat, ofSubviewAt dividerIndex: Int) -> CGFloat { 240 }
    func splitView(_ splitView: NSSplitView, constrainMaxCoordinate proposedMaximumPosition: CGFloat, ofSubviewAt dividerIndex: Int) -> CGFloat { min(380,splitView.bounds.width-300) }
    func windowDidResize(_ notification: Notification) { let record = rendered; rendered = nil; showDetail(record) }
    func windowWillClose(_ notification: Notification) { timer?.invalidate(); timer = nil }
}
