import AppKit
import CoreText
import Darwin
import Foundation

private let appDisplayName = "断点复原浮窗"

@main
struct FloatingRecoveryApp {
    static func main() {
        do {
            let configuration = try AppConfiguration.parse(arguments: Array(CommandLine.arguments.dropFirst()))
            let application = NSApplication.shared
            application.setActivationPolicy(.accessory)
            let delegate = FloatingPanelDelegate(configuration: configuration)
            application.delegate = delegate
            application.run()
        } catch {
            let text = "\(appDisplayName): \(error.localizedDescription)\n"
            FileHandle.standardError.write(Data(text.utf8))
            exit(64)
        }
    }
}

private struct AppConfiguration {
    let recoveryScriptURL: URL
    let stateDirectoryURL: URL
    let codexBundleIdentifier: String
    let diagnosticsURL: URL?
    let testMode: Bool
    let testCodexRunning: Bool?

    private static let defaultBundleIdentifier = "com.openai.codex"

    static func parse(arguments: [String]) throws -> AppConfiguration {
        let productionStateDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/ResumeDesk/Recovery", isDirectory: true)
            .standardizedFileURL
            .resolvingSymlinksInPath()
        guard let recoveryScriptURL = Bundle.main.url(forResource: "recovery", withExtension: "py", subdirectory: "Recovery") else {
            throw UsageError.missingBundledRecoveryScript
        }

        var stateDirectoryURL: URL?
        var bundleIdentifier = defaultBundleIdentifier
        var diagnosticsURL: URL?
        var testMode = false
        var testCodexRunning: Bool?

        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--state-dir":
                index += 1
                guard index < arguments.count else { throw UsageError.missingValue(argument) }
                stateDirectoryURL = URL(fileURLWithPath: arguments[index], isDirectory: true).standardizedFileURL
            case "--codex-bundle-id":
                index += 1
                guard index < arguments.count, !arguments[index].isEmpty else { throw UsageError.missingValue(argument) }
                bundleIdentifier = arguments[index]
            case "--diagnostics-path":
                index += 1
                guard index < arguments.count else { throw UsageError.missingValue(argument) }
                diagnosticsURL = URL(fileURLWithPath: arguments[index]).standardizedFileURL
            case "--test-mode":
                testMode = true
            case "--test-codex-running":
                index += 1
                guard index < arguments.count else { throw UsageError.missingValue(argument) }
                switch arguments[index].lowercased() {
                case "1", "true", "yes": testCodexRunning = true
                case "0", "false", "no": testCodexRunning = false
                default: throw UsageError.invalidBoolean(arguments[index])
                }
            case "--help", "-h":
                print(usageText)
                exit(0)
            default:
                throw UsageError.unknownArgument(argument)
            }
            index += 1
        }

        guard !bundleIdentifier.isEmpty else { throw UsageError.invalidBundleIdentifier }
        if testCodexRunning != nil && !testMode {
            throw UsageError.testVisibilityNeedsTestMode
        }

        let resolvedStateDirectory: URL
        if let stateDirectoryURL {
            resolvedStateDirectory = stateDirectoryURL
        } else if testMode {
            let identifier = ProcessInfo.processInfo.processIdentifier
            resolvedStateDirectory = FileManager.default.temporaryDirectory
                .appendingPathComponent("ResumeDeskFloatingPanel-test-\(identifier)", isDirectory: true)
                .standardizedFileURL
        } else {
            resolvedStateDirectory = productionStateDirectory
        }

        if testMode && resolvedStateDirectory.resolvingSymlinksInPath() == productionStateDirectory {
            throw UsageError.testModeCannotUseProductionState
        }

        try FileManager.default.createDirectory(at: resolvedStateDirectory, withIntermediateDirectories: true)
        return AppConfiguration(
            recoveryScriptURL: recoveryScriptURL.standardizedFileURL,
            stateDirectoryURL: resolvedStateDirectory,
            codexBundleIdentifier: bundleIdentifier,
            diagnosticsURL: diagnosticsURL,
            testMode: testMode,
            testCodexRunning: testCodexRunning
        )
    }

    private static let usageText = """
    用法：断点复原.app/Contents/MacOS/FloatingRecoveryPanel [选项]
      --state-dir PATH             指定 panel 运行数据目录
      --codex-bundle-id ID         观察的目标应用 bundle identifier（默认 com.openai.codex）
      --diagnostics-path PATH      在指定位置按状态变化写入小份 UI 状态 JSON
      --test-mode                  只使用临时或显式测试运行目录；不打开真实 Codex 任务
      --test-codex-running BOOL    仅测试模式：模拟目标是否正在运行（true/false）
    """
}

private enum UsageError: LocalizedError {
    case missingValue(String)
    case unknownArgument(String)
    case invalidBoolean(String)
    case invalidBundleIdentifier
    case testVisibilityNeedsTestMode
    case testModeCannotUseProductionState
    case missingBundledRecoveryScript

    var errorDescription: String? {
        switch self {
        case .missingValue(let option): return "缺少 \(option) 的值。"
        case .unknownArgument(let value): return "不认识的参数：\(value)。使用 --help 查看选项。"
        case .invalidBoolean(let value): return "--test-codex-running 只能是 true 或 false，收到：\(value)。"
        case .invalidBundleIdentifier: return "目标应用的 bundle identifier 不能为空。"
        case .testVisibilityNeedsTestMode: return "--test-codex-running 只允许与 --test-mode 一起使用。"
        case .testModeCannotUseProductionState: return "测试模式不能使用正式的 运行数据 目录或指向它的软链接。"
        case .missingBundledRecoveryScript: return "应用包缺少 Recovery/recovery.py。请重新构建或重新下载。"
        }
    }
}

private struct PanelView: Codable {
    let schemaVersion: Int
    let revision: String
    let updatedAt: String?
    let projects: [PanelProject]
    let notice: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case revision
        case updatedAt = "updated_at"
        case projects
        case notice
    }
}

private struct PanelProject: Codable {
    let key: String
    let label: String
    let sourceKind: String?
    let updatedAt: String?
    let items: [PanelItem]

    // Read display metadata already included in queued rows; never change ownership.
    var displayParts: (code: String, name: String) {
        let metadata = items.compactMap(\.project).first
        let rawName = metadata?.name?.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = (rawName?.isEmpty == false ? rawName! : label)
        let candidates = [metadata?.code, metadata?.path.map { URL(fileURLWithPath: $0).lastPathComponent }, name, label]
        var code = ""
        for candidate in candidates.compactMap({ $0 }) {
            if let range = candidate.range(of: "^[A-Za-z0-9]{3}(?=[\\s·._-]|$)", options: .regularExpression) {
                code = String(candidate[range]); break
            }
        }
        guard !code.isEmpty else { return ("", name) }
        let prefix = "^" + NSRegularExpression.escapedPattern(for: code) + "[\\s·._-]+"
        let remainder = name.replacingOccurrences(of: prefix, with: "", options: .regularExpression)
        return (code, remainder == code ? "" : remainder)
    }

    enum CodingKeys: String, CodingKey {
        case key
        case label
        case sourceKind = "source_kind"
        case updatedAt = "updated_at"
        case items
    }
}

private struct ProjectDisplayMetadata: Codable {
    let name: String?
    let code: String?
    let path: String?
}

// This is sourced from the verified panel row. The floating panel must never
// infer any of these three sentences from a review point or a title.
private struct ResumeContext: Codable, Equatable {
    let previousFocus: String?
    let currentState: String?
    let nextStep: String?

    enum CodingKeys: String, CodingKey {
        case previousFocus = "previous_focus"
        case currentState = "current_state"
        case nextStep = "next_step"
    }
}

private struct ResumeCardCopy: Equatable {
    let previousFocus: String
    let currentState: String
    let nextStep: String

    var accessibilityText: String {
        "上次在做：\(previousFocus)\n当前停点：\(currentState)\n建议下一步：\(nextStep)"
    }
}

private struct PanelNavigation: Codable {
    let kind: String
    let url: String

    func destination(for id: String) -> URL? {
        guard UUID(uuidString: id) != nil else { return nil }
        let prefix: String
        switch kind {
        case "chatgpt": prefix = "https://chatgpt.com/c/"
        case "codex": prefix = "codex://threads/"
        default: return nil
        }
        // Accept only the backend's original-conversation route. No query,
        // credentials, redirects, or URLs supplied by message text are allowed.
        guard url == prefix + id.lowercased() else { return nil }
        return URL(string: url)
    }
}

private struct PanelItem: Codable {
    let id: String
    let title: String
    let reviewPoint: String
    let fingerprint: String
    let updatedAt: String?
    let sourceReportAt: String?
    let actionToken: String
    let actionable: Bool
    let project: ProjectDisplayMetadata?
    let resumeContext: ResumeContext?
    let navigation: PanelNavigation?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case reviewPoint = "review_point"
        case fingerprint
        case updatedAt = "updated_at"
        case sourceReportAt = "source_report_at"
        case actionToken = "action_token"
        case actionable
        case project
        case resumeContext = "resume_context"
        case navigation
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decode(String.self, forKey: .title)
        reviewPoint = try container.decode(String.self, forKey: .reviewPoint)
        fingerprint = try container.decode(String.self, forKey: .fingerprint)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        sourceReportAt = try container.decodeIfPresent(String.self, forKey: .sourceReportAt)
        actionToken = try container.decode(String.self, forKey: .actionToken)
        actionable = try container.decodeIfPresent(Bool.self, forKey: .actionable) ?? true
        project = try container.decodeIfPresent(ProjectDisplayMetadata.self, forKey: .project)
        resumeContext = try container.decodeIfPresent(ResumeContext.self, forKey: .resumeContext)
        navigation = try container.decodeIfPresent(PanelNavigation.self, forKey: .navigation)
    }
}

private extension PanelItem {
    func resumeCardCopy() -> ResumeCardCopy {
        func verified(_ value: String?) -> String? {
            guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
            return value
        }
        let prior = actionable ? (verified(resumeContext?.previousFocus) ?? "尚未核实") : "尚未核实"
        let next = actionable ? (verified(resumeContext?.nextStep) ?? "尚未核实") : "尚未核实"
        if actionable, let current = verified(resumeContext?.currentState) {
            return ResumeCardCopy(previousFocus: prior, currentState: current, nextStep: next)
        }
        if let reviewPoint = verified(reviewPoint) {
            // Old rows have only one reviewed sentence. It remains useful as a
            // current stop, but it is deliberately marked as pending recheck.
            return ResumeCardCopy(previousFocus: prior, currentState: "\(reviewPoint)（沿用原停点，等待核对）", nextStep: next)
        }
        return ResumeCardCopy(previousFocus: prior, currentState: "尚未核实", nextStep: next)
    }

    func listStopLine() -> String {
        let current = resumeContext?.currentState?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return actionable && !current.isEmpty ? current : reviewPoint
    }
}

private struct PanelErrorResponse: Decodable {
    let error: String?
}

private enum PanelAction: String {
    case take
    case dismiss

    var displayName: String {
        switch self {
        case .take: return "接手"
        case .dismiss: return "不再关注"
        }
    }
}

private enum BackendError: LocalizedError {
    case processLaunch(String)
    case invalidResponse(String)
    case rejected(String)

    var errorDescription: String? {
        switch self {
        case .processLaunch(let message): return "无法读取恢复队列：\(message)"
        case .invalidResponse(let message): return "恢复队列返回了无法识别的数据：\(message)"
        case .rejected(let message): return message
        }
    }
}

private final class RecoveryBackend {
    private let configuration: AppConfiguration
    private let executionQueue = DispatchQueue(label: "ResumeDesk.RecoveryFloatingPanel.backend")

    init(configuration: AppConfiguration) {
        self.configuration = configuration
    }

    func read(completion: @escaping (Result<PanelView, BackendError>) -> Void) {
        run(arguments: ["panel-read"], completion: completion)
    }

    func perform(item: PanelItem, action: PanelAction, completion: @escaping (Result<PanelView, BackendError>) -> Void) {
        run(
            arguments: ["panel-action", "--id", item.id, "--action", action.rawValue, "--token", item.actionToken],
            completion: completion
        )
    }

    private func run(arguments: [String], completion: @escaping (Result<PanelView, BackendError>) -> Void) {
        let configuration = self.configuration
        executionQueue.async {
            let result: Result<PanelView, BackendError>
            do {
                let process = Process()
                let standardOutput = Pipe()
                let standardError = Pipe()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                process.arguments = ["-B", configuration.recoveryScriptURL.path, "--state-dir", configuration.stateDirectoryURL.path] + arguments
                process.standardOutput = standardOutput
                process.standardError = standardError
                try process.run()

                // Both streams must be drained while the child is alive: a large JSON response can otherwise
                // fill the pipe and block before waitUntilExit returns. Requests share one serial queue so an
                // earlier panel-read cannot apply over a later panel-action result.
                let captures = DispatchGroup()
                let outputCapture = DataCapture()
                let errorCapture = DataCapture()
                captures.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    outputCapture.set(standardOutput.fileHandleForReading.readDataToEndOfFile())
                    captures.leave()
                }
                captures.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    errorCapture.set(standardError.fileHandleForReading.readDataToEndOfFile())
                    captures.leave()
                }
                process.waitUntilExit()
                captures.wait()

                let outputData = outputCapture.value
                let errorData = errorCapture.value
                let standardErrorText = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

                if process.terminationStatus != 0 {
                    // recovery.py emits its machine-readable error object to stderr on a non-zero exit.
                    let backendMessage = (try? JSONDecoder().decode(PanelErrorResponse.self, from: errorData).error)
                        ?? (try? JSONDecoder().decode(PanelErrorResponse.self, from: outputData).error)
                    let message = backendMessage ?? (standardErrorText.isEmpty ? "后端命令退出码为 \(process.terminationStatus)。" : standardErrorText)
                    result = .failure(.rejected(message))
                } else {
                    do {
                        result = .success(try JSONDecoder().decode(PanelView.self, from: outputData))
                    } catch {
                        let preview = String(data: outputData.prefix(400), encoding: .utf8) ?? "空输出"
                        result = .failure(.invalidResponse("\(error.localizedDescription)；输出：\(preview)"))
                    }
                }
            } catch {
                result = .failure(.processLaunch(error.localizedDescription))
            }
            DispatchQueue.main.async { completion(result) }
        }
    }
}

private final class DataCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var storage = Data()

    func set(_ data: Data) {
        lock.lock()
        storage = data
        lock.unlock()
    }

    var value: Data {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

private struct PanelUIState: Codable {
    let schemaVersion: Int
    let frame: SavedFrame?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case frame
    }
}

private struct SavedFrame: Codable {
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat

    init(frame: NSRect) {
        x = frame.origin.x
        y = frame.origin.y
        width = frame.size.width
        height = frame.size.height
    }

    var rect: NSRect {
        NSRect(x: x, y: y, width: width, height: height)
    }
}

private final class PanelUIStateStore {
    private let url: URL

    init(stateDirectoryURL: URL) {
        url = stateDirectoryURL.appendingPathComponent("panel-ui.json")
    }

    func load() -> PanelUIState? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(PanelUIState.self, from: data)
    }

    func save(frame: NSRect) {
        let state = PanelUIState(schemaVersion: 2, frame: SavedFrame(frame: frame))
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(state).write(to: url, options: .atomic)
        } catch {
            // UI state is optional. A failed local preference write must never affect the recovery queue.
            NSLog("%@：无法保存浮窗布局：%@", appDisplayName, error.localizedDescription)
        }
    }
}

private final class NonActivatingFloatingPanel: NSPanel {
    var acceptingConfirmation = false
    private var dragAnchor: (pointer: NSPoint, origin: NSPoint)?
    private var dragCursorPushed = false
    var dragPhase: String { dragAnchor == nil ? "idle" : (dragCursorPushed ? "dragging" : "armed") }
    override var canBecomeKey: Bool { acceptingConfirmation || dragAnchor != nil }
    override var canBecomeMain: Bool { false }

    private func screenPoint(for event: NSEvent) -> NSPoint {
        if let eventWindow = event.window { return eventWindow.convertPoint(toScreen: event.locationInWindow) }
        return event.locationInWindow
    }

    func beginLocalDrag(with event: NSEvent) {
        guard !acceptingConfirmation, attachedSheet == nil else { return }
        finishLocalDrag()
        dragAnchor = (screenPoint(for: event), frame.origin)
        // A non-activating panel may temporarily take keyboard focus for Escape.
        makeKey()
    }

    func continueLocalDrag(with event: NSEvent) {
        guard let anchor = dragAnchor else { return }
        let point = screenPoint(for: event)
        let dx = point.x - anchor.pointer.x, dy = point.y - anchor.pointer.y
        guard dragCursorPushed || hypot(dx, dy) >= 1 else { return }
        if !dragCursorPushed {
            NSCursor.closedHand.push()
            dragCursorPushed = true
        }
        // Movement and opacity share the same local mouse-dragged input boundary.
        alphaValue = 0.5
        setFrameOrigin(NSPoint(x: anchor.origin.x + dx, y: anchor.origin.y + dy))
    }

    private func clearLocalDrag() {
        dragAnchor = nil
        alphaValue = 1
        if dragCursorPushed { NSCursor.pop(); dragCursorPushed = false }
    }

    func finishLocalDrag() {
        let hadDrag = dragAnchor != nil
        clearLocalDrag()
        if hadDrag && isKeyWindow && !acceptingConfirmation { super.resignKey() }
    }

    override func sendEvent(_ event: NSEvent) {
        if dragAnchor != nil {
            switch event.type {
            case .leftMouseDragged:
                continueLocalDrag(with: event)
                return
            case .leftMouseUp:
                finishLocalDrag()
                return
            case .keyDown where event.keyCode == 53:
                finishLocalDrag()
                return
            case .rightMouseDown, .otherMouseDown:
                finishLocalDrag()
            default: break
            }
        }
        super.sendEvent(event)
    }

    // Unhandled background presses use the same lifecycle as the explicit header.
    override func mouseDown(with event: NSEvent) { beginLocalDrag(with: event) }
    override func mouseDragged(with event: NSEvent) { continueLocalDrag(with: event) }
    override func mouseUp(with event: NSEvent) { finishLocalDrag() }
    override func cancelOperation(_ sender: Any?) { finishLocalDrag() }

    #if compiler(>=6.2)
    @available(macOS 26.0, *)
    override func mouseCancelled(with event: NSEvent) { finishLocalDrag() }
    #endif

    override func orderOut(_ sender: Any?) {
        finishLocalDrag()
        super.orderOut(sender)
    }
    override func resignKey() {
        clearLocalDrag()
        super.resignKey()
    }
}

private class RecoveryItemButton: NSButton {
    var item: PanelItem?
    override var alignmentRectInsets: NSEdgeInsets { NSEdgeInsetsZero }
}

// A long title supplies height, never a minimum width. NSButton's default intrinsic
// width is its unwrapped title and can otherwise force the whole NSPanel off-screen.
private final class WrappingRecoveryTitleButton: RecoveryItemButton {
    private var measuredWidth: CGFloat = 0

    override var intrinsicContentSize: NSSize {
        let width = max(1, bounds.width > 0 ? bounds.width - 8 : 150)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        let rectangle = (title as NSString).boundingRect(
            with: NSSize(width: width, height: CGFloat.greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: font ?? NSFont.systemFont(ofSize: 13), .paragraphStyle: paragraph]
        )
        return NSSize(width: NSView.noIntrinsicMetric, height: max(22, ceil(rectangle.height) + 6))
    }

    override func layout() {
        super.layout()
        if abs(bounds.width - measuredWidth) > 0.5 {
            measuredWidth = bounds.width
            invalidateIntrinsicContentSize()
        }
    }
}

private final class ProjectHeadingButton: NSButton {
    // The entire heading is one accessible click target, including its labels.
    override func hitTest(_ point: NSPoint) -> NSView? {
        super.hitTest(point) == nil ? nil : self
    }
    override var intrinsicContentSize: NSSize { NSSize(width: NSView.noIntrinsicMetric, height: NSView.noIntrinsicMetric) }
}

private final class TextRegionButton: RecoveryItemButton {
    var onHoverEntered: ((TextRegionButton) -> Void)?
    var onHoverExited: ((TextRegionButton) -> Void)?
    var onKeyboardFocus: ((TextRegionButton) -> Void)?
    var onKeyboardBlur: ((TextRegionButton) -> Void)?
    private var hoverTrackingArea: NSTrackingArea?

    override var intrinsicContentSize: NSSize { NSSize(width: NSView.noIntrinsicMetric, height: NSView.noIntrinsicMetric) }

    override func updateTrackingAreas() {
        if let hoverTrackingArea { removeTrackingArea(hoverTrackingArea) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        hoverTrackingArea = area
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) { onHoverEntered?(self) }
    override func mouseExited(with event: NSEvent) { onHoverExited?(self) }

    override func becomeFirstResponder() -> Bool {
        let accepted = super.becomeFirstResponder()
        if accepted { onKeyboardFocus?(self) }
        return accepted
    }

    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned { onKeyboardBlur?(self) }
        return resigned
    }
}

private final class ResumeHoverPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    init() {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 130),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        hidesOnDeactivate = false
        isMovable = false
    }
}

private final class ResumeCardDocumentView: NSView {
    override var isFlipped: Bool { true }
}

private final class ResumeHoverCardView: NSView {
    var onPointerEntered: (() -> Void)?
    var onPointerExited: (() -> Void)?

    private let scrollView = NSScrollView()
    private let documentView = ResumeCardDocumentView()
    private var hoverTrackingArea: NSTrackingArea?
    private var preferredTextHeight: CGFloat = 84
    private let cardWidth: CGFloat = 360
    private let contentInset: CGFloat = 20
    private let bodyFont = NSFont.systemFont(ofSize: 14)
    private let labelFont = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
    private let labelInk = NSColor(srgbRed: 0.37, green: 0.40, blue: 0.44, alpha: 1)

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.borderWidth = 0.6
        layer?.borderColor = NSColor.panelBorder.cgColor
        layer?.backgroundColor = NSColor.white.cgColor
        layer?.masksToBounds = true
        setAccessibilityElement(true)
        setAccessibilityRole(.group)

        scrollView.borderType = .noBorder
        scrollView.drawsBackground = false
        scrollView.hasHorizontalScroller = false
        scrollView.hasVerticalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.scrollerStyle = .overlay
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = documentView
        addSubview(scrollView)
        NSLayoutConstraint.activate([
            scrollView.leadingAnchor.constraint(equalTo: leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: trailingAnchor),
            scrollView.topAnchor.constraint(equalTo: topAnchor),
            scrollView.bottomAnchor.constraint(equalTo: bottomAnchor)
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func updateTrackingAreas() {
        if let hoverTrackingArea { removeTrackingArea(hoverTrackingArea) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        hoverTrackingArea = area
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) { onPointerEntered?() }
    override func mouseExited(with event: NSEvent) { onPointerExited?() }

    var isScrollable: Bool { scrollView.hasVerticalScroller }
    var documentTextHeight: CGFloat { documentView.frame.height }
    var visibleTextHeight: CGFloat { scrollView.contentView.bounds.height }

    private func field(_ value: String, font: NSFont, color: NSColor,
                       width: CGFloat, multiline: Bool = true) -> NSTextField {
        let view = NSTextField(wrappingLabelWithString: value)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = multiline ? .byWordWrapping : .byTruncatingTail
        if multiline {
            paragraph.minimumLineHeight = 21
            paragraph.maximumLineHeight = 21
        }
        view.attributedStringValue = NSAttributedString(string: value, attributes: [
            .font: font, .foregroundColor: color, .paragraphStyle: paragraph
        ])
        view.font = font
        view.textColor = color
        view.isSelectable = multiline
        view.maximumNumberOfLines = multiline ? 0 : 1
        view.lineBreakMode = paragraph.lineBreakMode
        view.cell?.wraps = multiline
        let measured = view.attributedStringValue.boundingRect(
            with: NSSize(width: width, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading]
        )
        // NSTextField includes cell insets and fallback-font wrapping that can
        // differ from NSString measurement on older supported macOS versions.
        let nativeHeight = view.cell?.cellSize(forBounds: NSRect(
            x: 0, y: 0, width: width, height: 10000
        )).height ?? 0
        view.frame = NSRect(x: 0, y: 0, width: width,
                            height: multiline ? max(23, ceil(max(measured.height, nativeHeight)) + 5) : 19)
        return view
    }

    @discardableResult
    private func separator(at y: CGFloat) -> CGFloat {
        let line = NSView(frame: NSRect(x: contentInset, y: y,
                                       width: cardWidth - contentInset * 2, height: 0.6))
        line.wantsLayer = true
        line.layer?.backgroundColor = NSColor.panelBorder.cgColor
        documentView.addSubview(line)
        return y + 0.6
    }

    func configure(copy: ResumeCardCopy, projectLabel: String? = nil) -> NSSize {
        documentView.subviews.forEach { $0.removeFromSuperview() }
        let bodyWidth = cardWidth - contentInset * 2
        let heading = field("接续提示", font: labelFont, color: labelInk, width: 76, multiline: false)
        heading.frame.origin = NSPoint(x: contentInset, y: 17)
        documentView.addSubview(heading)
        if let projectLabel, !projectLabel.isEmpty {
            let project = field(projectLabel, font: .systemFont(ofSize: 12),
                                color: labelInk, width: bodyWidth - 92, multiline: false)
            let caption = NSMutableAttributedString(attributedString: project.attributedStringValue)
            let alignment = NSMutableParagraphStyle()
            alignment.alignment = .right
            alignment.lineBreakMode = .byTruncatingTail
            caption.addAttribute(.paragraphStyle, value: alignment, range: NSRange(location: 0, length: caption.length))
            project.attributedStringValue = caption
            project.frame.origin = NSPoint(x: contentInset + 92, y: 17)
            project.toolTip = projectLabel
            documentView.addSubview(project)
        }
        var y = separator(at: 47) + 18
        let sections = [
            ("上次在做", copy.previousFocus, "resume-previous"),
            ("当前停点", copy.currentState, "resume-current"),
            ("建议下一步", copy.nextStep, "resume-next")
        ]
        for (index, section) in sections.enumerated() {
            let label = field(section.0, font: labelFont, color: labelInk,
                              width: bodyWidth, multiline: false)
            let body = field(section.1, font: bodyFont, color: .panelInk, width: bodyWidth)
            body.identifier = NSUserInterfaceItemIdentifier(section.2)
            if index == 2 {
                let height = 14 + label.frame.height + 6 + body.frame.height + 14
                let background = ResumeCardDocumentView(frame: NSRect(
                    x: 10, y: y, width: cardWidth - 20, height: height
                ))
                background.wantsLayer = true
                background.layer?.cornerRadius = 10
                background.layer?.backgroundColor = NSColor(
                    srgbRed: 0.954, green: 0.961, blue: 0.969, alpha: 1
                ).cgColor
                label.frame.origin = NSPoint(x: contentInset - 10, y: 14)
                body.frame.origin = NSPoint(x: contentInset - 10, y: 14 + label.frame.height + 6)
                background.addSubview(label)
                background.addSubview(body)
                documentView.addSubview(background)
                y += height + 10
            } else {
                label.frame.origin = NSPoint(x: contentInset, y: y)
                body.frame.origin = NSPoint(x: contentInset, y: y + label.frame.height + 6)
                documentView.addSubview(label)
                documentView.addSubview(body)
                y += label.frame.height + 6 + body.frame.height
                y = index == 0 ? separator(at: y + 17) + 18 : y + 18
            }
        }
        preferredTextHeight = ceil(y)
        documentView.setFrameSize(NSSize(width: cardWidth, height: preferredTextHeight))
        documentView.scroll(.zero)
        setAccessibilityLabel("接续说明。\(copy.accessibilityText)")
        documentView.setAccessibilityLabel(copy.accessibilityText)
        return NSSize(width: cardWidth, height: preferredTextHeight)
    }

    func displaySize(maximumHeight: CGFloat) -> NSSize {
        let height = min(preferredTextHeight, maximumHeight)
        frame.size = NSSize(width: cardWidth, height: height)
        layoutSubtreeIfNeeded()
        scrollView.hasVerticalScroller = preferredTextHeight > height + 0.5
        scrollView.tile()
        documentView.setFrameSize(NSSize(width: cardWidth, height: max(preferredTextHeight, height)))
        return frame.size
    }
}


private enum ResumeHoverPlacement {
    private static let gap: CGFloat = 10

    static func frame(anchor: NSRect, host: NSRect, cardSize: NSSize, visibleFrame: NSRect) -> NSRect {
        guard visibleFrame.width >= cardSize.width, visibleFrame.height >= cardSize.height else {
            return NSRect(x: visibleFrame.minX, y: visibleFrame.minY, width: min(cardSize.width, visibleFrame.width), height: min(cardSize.height, visibleFrame.height))
        }

        func alignedY() -> CGFloat {
            min(max(anchor.midY - cardSize.height / 2, visibleFrame.minY), visibleFrame.maxY - cardSize.height)
        }
        func alignedX() -> CGFloat {
            min(max(anchor.midX - cardSize.width / 2, visibleFrame.minX), visibleFrame.maxX - cardSize.width)
        }
        let left = NSRect(x: host.minX - gap - cardSize.width, y: alignedY(), width: cardSize.width, height: cardSize.height)
        if left.minX >= visibleFrame.minX { return left }
        let right = NSRect(x: host.maxX + gap, y: alignedY(), width: cardSize.width, height: cardSize.height)
        if right.maxX <= visibleFrame.maxX { return right }
        let below = NSRect(x: alignedX(), y: host.minY - gap - cardSize.height, width: cardSize.width, height: cardSize.height)
        if below.minY >= visibleFrame.minY { return below }
        let above = NSRect(x: alignedX(), y: host.maxY + gap, width: cardSize.width, height: cardSize.height)
        if above.maxY <= visibleFrame.maxY { return above }

        // Extremely small displays cannot fit an exterior card. Keep the card
        // entirely on screen and bias it away from the action-button edge.
        return NSRect(x: visibleFrame.minX, y: alignedY(), width: cardSize.width, height: cardSize.height)
    }
}

private final class ResumeHoverPresenter {
    private let hoverPanel = ResumeHoverPanel()
    private let cardView = ResumeHoverCardView(frame: NSRect(x: 0, y: 0, width: 360, height: 130))
    private var showWorkItem: DispatchWorkItem?
    private var hideWorkItem: DispatchWorkItem?
    private weak var pendingSource: TextRegionButton?
    private var pendingItem: PanelItem?
    private(set) var displayedItemID: String?
    private(set) var displayedCopy: ResumeCardCopy?

    init() {
        hoverPanel.contentView = cardView
        cardView.onPointerEntered = { [weak self] in self?.cardPointerEntered() }
        cardView.onPointerExited = { [weak self] in self?.cardPointerExited() }
    }

    var isVisible: Bool { hoverPanel.isVisible }
    var frame: NSRect { hoverPanel.frame }
    var canBecomeKey: Bool { hoverPanel.canBecomeKey }
    var isKeyWindow: Bool { hoverPanel.isKeyWindow }

    func sourcePointerEntered(_ source: TextRegionButton) {
        clearTimers()
        hoverPanel.orderOut(nil)
        displayedItemID = nil
        displayedCopy = nil
        guard let item = source.item else { return }
        pendingSource = source
        pendingItem = item
        let work = DispatchWorkItem { [weak self, weak source] in
            guard let self, let source, self.pendingSource === source, self.pendingItem?.id == item.id else { return }
            self.show(item: item, from: source)
        }
        showWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(400), execute: work)
    }

    func sourcePointerExited(_ source: TextRegionButton) {
        guard pendingSource === source || displayedItemID == source.item?.id else { return }
        scheduleHide()
    }

    func cardPointerEntered() {
        hideWorkItem?.cancel()
        hideWorkItem = nil
    }

    func cardPointerExited() { scheduleHide() }

    func clear() {
        clearTimers()
        pendingSource = nil
        pendingItem = nil
        displayedItemID = nil
        displayedCopy = nil
        hoverPanel.orderOut(nil)
    }

    private func show(item: PanelItem, from source: TextRegionButton) {
        guard let sourceWindow = source.window else { clear(); return }
        let copy = item.resumeCardCopy()
        let sourceFrame = sourceWindow.convertToScreen(source.convert(source.bounds, to: nil))
        let visibleFrame = sourceWindow.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? NSScreen.screens.first?.visibleFrame ?? sourceFrame
        _ = cardView.configure(copy: copy, projectLabel: item.project?.name)
        // Never let long verified text extend below the visible screen. The
        // card keeps all three lines in its own scroll view when capped.
        let size = cardView.displaySize(maximumHeight: min(480, max(84, visibleFrame.height - 24)))
        let target = ResumeHoverPlacement.frame(anchor: sourceFrame, host: sourceWindow.frame, cardSize: size, visibleFrame: visibleFrame)
        hoverPanel.setFrame(target, display: false)
        hoverPanel.orderFront(nil)
        displayedItemID = item.id
        displayedCopy = copy
    }

    private func scheduleHide() {
        showWorkItem?.cancel()
        showWorkItem = nil
        hideWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.clear() }
        hideWorkItem = work
        // Crossing the source button, its padding, and the exterior gap must
        // not close the card before the pointer can enter it.
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(400), execute: work)
    }

    private func clearTimers() {
        showWorkItem?.cancel()
        showWorkItem = nil
        hideWorkItem?.cancel()
        hideWorkItem = nil
    }
}

private final class CountBadge: NSView {
    let count: Int
    private let line: CTLine
    private let ink: CGRect
    let diameter: CGFloat

    init(count: Int) {
        self.count = count
        let string = NSAttributedString(string: String(count), attributes: [.font: NSFont.systemFont(ofSize: 13, weight: .bold), .foregroundColor: NSColor.white])
        line = CTLineCreateWithAttributedString(string)
        ink = CTLineGetBoundsWithOptions(line, .useGlyphPathBounds)
        diameter = max(22, ceil(max(ink.width, ink.height) + 12))
        super.init(frame: .zero)
        setAccessibilityElement(true)
        setAccessibilityRole(.staticText)
        setAccessibilityLabel("\(count)条待接续任务")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override var intrinsicContentSize: NSSize { NSSize(width: diameter, height: diameter) }
    override var isOpaque: Bool { false }
    var centeredInkRect: NSRect {
        NSRect(x: bounds.midX - ink.width / 2, y: bounds.midY - ink.height / 2, width: ink.width, height: ink.height)
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor(srgbRed: 0.96, green: 0.06, blue: 0.12, alpha: 1).setFill()
        NSBezierPath(ovalIn: bounds).fill()
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        context.saveGState()
        context.textMatrix = .identity
        context.textPosition = CGPoint(x: bounds.midX - ink.midX, y: bounds.midY - ink.midY)
        CTLineDraw(line, context)
        context.restoreGState()
    }
}

private extension NSColor {
    static let panelInk = NSColor(srgbRed: 0.067, green: 0.067, blue: 0.067, alpha: 1)
    static let projectInk = NSColor(srgbRed: 0.2, green: 0.2, blue: 0.2, alpha: 1)
    static let projectNameInk = NSColor(srgbRed: 0.4, green: 0.4, blue: 0.4, alpha: 1)
    static let panelGray = NSColor(srgbRed: 0.945, green: 0.953, blue: 0.961, alpha: 1)
    static let panelBorder = NSColor(srgbRed: 0.85, green: 0.87, blue: 0.89, alpha: 1)
}

private final class FlippedDocumentView: NSView {
    override var isFlipped: Bool { true }

    override func layout() {
        super.layout()
        // NSScrollView may retain its earlier minimum document width when a
        // legacy scroller appears. The document must track the current clip.
        if let width = enclosingScrollView?.contentView.bounds.width, abs(frame.width - width) > 0.5 {
            setFrameSize(NSSize(width: width, height: frame.height))
        }
    }
}

private final class PanelDragHandle: NSView {
    override var mouseDownCanMoveWindow: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        (window as? NonActivatingFloatingPanel)?.beginLocalDrag(with: event)
    }
    override func mouseDragged(with event: NSEvent) {
        (window as? NonActivatingFloatingPanel)?.continueLocalDrag(with: event)
    }
    override func mouseUp(with event: NSEvent) {
        (window as? NonActivatingFloatingPanel)?.finishLocalDrag()
    }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
}

private final class PanelWindowController: NSObject, NSWindowDelegate {
    private let panel: NonActivatingFloatingPanel
    private let resumeHover = ResumeHoverPresenter()
    private let headerLabel = NSTextField(labelWithString: "待接续任务")
    private let metadataLabel = NSTextField(labelWithString: "正在读取…")
    private let noticeLabel = NSTextField(wrappingLabelWithString: "")
    private let scrollView = NSScrollView()
    private let contentStack = NSStackView()
    private var hasRestoredWindowFrame = false
    private var dismissAlert: NSAlert?
    private var confirmationKeyMonitor: Any?
    private let headerDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = .current
        formatter.dateFormat = "M月d日"
        return formatter
    }()
    private let fractionalISO8601Formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private let standardISO8601Formatter = ISO8601DateFormatter()

    var onToggleProject: ((String) -> Void)?
    var onNavigate: ((PanelItem) -> Void)?
    var onAction: ((PanelItem, PanelAction) -> Void)?
    var onWindowMovedByUser: ((NSRect) -> Void)?

    override init() {
        panel = NonActivatingFloatingPanel(
            contentRect: NSRect(x: 0, y: 0, width: 452, height: 720),
            styleMask: [.titled, .resizable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        super.init()
        configureWindow()
        configureContent()
    }

    func restoreWindowFrame(_ frame: SavedFrame?) {
        guard !hasRestoredWindowFrame else { return }
        hasRestoredWindowFrame = true
        guard let frame else { return }
        var proposedFrame = frame.rect
        guard proposedFrame.width > 0, proposedFrame.height > 0 else { return }
        // Migrate only an out-of-range legacy width. Saved x/y remain authoritative.
        if !(440...460).contains(proposedFrame.width) { proposedFrame.size.width = 452 }
        let screen = NSScreen.screens.first(where: { $0.visibleFrame.intersects(proposedFrame) })
            ?? NSScreen.main
            ?? NSScreen.screens.first
        guard let screen else { return }
        panel.setFrame(clampedFrame(proposedFrame, within: screen.visibleFrame), display: false)
    }

    func showWithoutActivation() {
        panel.orderFrontRegardless()
    }

    func hide() {
        clearResumeHover()
        if let dismissAlert { panel.endSheet(dismissAlert.window, returnCode: .cancel) }
        panel.orderOut(nil)
    }

    func clearResumeHover() { resumeHover.clear() }

    func confirmDismiss(_ item: PanelItem, completion: @escaping (Bool) -> Void) {
        guard dismissAlert == nil else { return }
        let alert = NSAlert()
        alert.messageText = "不再关注这条对话？"
        alert.informativeText = "《\(item.title)》\n\n确认后不再提醒，原对话和内容会保留。"
        alert.alertStyle = .informational
        let cancel = alert.addButton(withTitle: "取消")
        let confirm = alert.addButton(withTitle: "确认不再关注")
        cancel.keyEquivalent = "\r"
        cancel.keyEquivalentModifierMask = []
        alert.window.defaultButtonCell = cancel.cell as? NSButtonCell
        confirm.keyEquivalent = ""
        confirm.contentTintColor = .projectInk
        dismissAlert = alert
        panel.acceptingConfirmation = true
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKey()
        alert.beginSheetModal(for: panel) { [weak self] response in
            if let monitor = self?.confirmationKeyMonitor { NSEvent.removeMonitor(monitor) }
            self?.confirmationKeyMonitor = nil
            self?.dismissAlert = nil
            self?.panel.acceptingConfirmation = false
            self?.panel.resignKey()
            // The original item/token remains captured, even if a new report arrives.
            completion(response == .alertSecondButtonReturn)
        }
        // NSAlert reserves Return for the default button; handle Escape explicitly
        // only while this app's confirmation sheet is open (no global key monitor).
        confirmationKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, let alert = dismissAlert else { return event }
            if event.keyCode == 53 {
                panel.endSheet(alert.window, returnCode: .alertFirstButtonReturn)
                return nil
            }
            return event
        }
    }

    var confirmationVisible: Bool { dismissAlert != nil }

    var isVisible: Bool { panel.isVisible }
    var frame: NSRect { panel.frame }
    var levelName: String { "floating" }
    var collectionBehaviorNames: [String] { ["canJoinAllSpaces", "fullScreenAuxiliary", "stationary"] }

    var layoutDiagnostics: WindowLayoutDiagnostics {
        // Scroller visibility can change the clip width during the first layout.
        scrollView.tile()
        panel.contentView?.layoutSubtreeIfNeeded()
        panel.contentView?.layoutSubtreeIfNeeded()
        func descendants(_ view: NSView) -> [NSView] {
            view.subviews.flatMap { [$0] + descendants($0) }
        }
        let controls = descendants(contentStack).compactMap { $0 as? RecoveryItemButton }
            .filter { $0.action == #selector(takeItem(_:)) || $0.action == #selector(dismissItem(_:)) }
        let frames = controls.map { panel.convertToScreen($0.convert($0.bounds, to: nil)) }
        let viewport = panel.convertToScreen(scrollView.contentView.convert(scrollView.contentView.bounds, to: nil))
        let visibleScreen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? .zero
        let documentWidth = scrollView.documentView?.frame.width ?? 0
        return WindowLayoutDiagnostics(
            window: SavedFrame(frame: panel.frame), visibleScreen: SavedFrame(frame: visibleScreen),
            viewportWidth: scrollView.contentView.bounds.width, documentWidth: documentWidth,
            actionButtons: frames.map { SavedFrame(frame: $0) },
            horizontalContentFits: documentWidth <= scrollView.contentView.bounds.width + 1
                && frames.allSatisfy { $0.minX >= viewport.minX - 1 && $0.maxX <= viewport.maxX + 1 },
            windowFitsScreen: visibleScreen.contains(panel.frame),
            reviewDate: metadataLabel.stringValue,
            projectHeadings: descendants(contentStack).compactMap { ($0 as? ProjectHeadingButton)?.accessibilityLabel() },
            badges: descendants(contentStack).compactMap { $0 as? CountBadge }.map {
                BadgeDiagnostics(count: $0.count, frame: SavedFrame(frame: $0.bounds), ink: SavedFrame(frame: $0.centeredInkRect))
            },
            outerBackgroundAlpha: panel.contentView?.layer?.backgroundColor?.alpha ?? -1,
            windowAlpha: panel.alphaValue,
            opaqueWindow: panel.isOpaque,
            dragPhase: panel.dragPhase,
            projectNameColor: "#666666",
            cards: descendants(contentStack).filter { $0.identifier?.rawValue.hasPrefix("card:") == true }.map { card in
                let children = descendants(card)
                let title = children.compactMap { $0 as? WrappingRecoveryTitleButton }.first!
                let body = children.compactMap { $0 as? NSTextField }.first { $0.identifier?.rawValue == "review-point" }!
                let textRect = (body.stringValue as NSString).boundingRect(
                    with: NSSize(width: max(1, body.bounds.width - 4), height: .greatestFiniteMagnitude),
                    options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: body.font!])
                let textRegion = children.compactMap { $0 as? TextRegionButton }.first!
                return CardLayoutDiagnostics(id: title.item!.id,
                    textRegion: SavedFrame(frame: panel.convertToScreen(textRegion.convert(textRegion.bounds, to: nil))),
                    cardAlpha: card.layer?.backgroundColor?.alpha ?? -1,
                    titleColorAlpha: title.contentTintColor?.alphaComponent ?? -1,
                    bodyColorAlpha: body.textColor?.alphaComponent ?? -1,
                    frame: SavedFrame(frame: panel.convertToScreen(card.convert(card.bounds, to: nil))),
                    titleHeight: title.bounds.height, titleRequiredHeight: title.intrinsicContentSize.height,
                    bodyHeight: body.bounds.height, bodyRequiredHeight: ceil(textRect.height),
                    title: title.title, reviewPoint: body.stringValue)
            }
        )
    }

    func cancelDrag() { panel.finishLocalDrag() }

    func scrollToTop() {
        scrollView.contentView.scroll(to: .zero)
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    func render(view: PanelView?, expandedProjectKey: String?, activeActionIDs: Set<String>, transientMessage: String?) {
        // Render removes and replaces source text regions. An existing exterior
        // card could otherwise retain an older row and must never outlive them.
        clearResumeHover()
        let previousOrigin = scrollView.contentView.bounds.origin
        contentStack.arrangedSubviews.forEach { subview in
            contentStack.removeArrangedSubview(subview)
            subview.removeFromSuperview()
        }

        if let transientMessage, !transientMessage.isEmpty {
            noticeLabel.stringValue = transientMessage
            noticeLabel.textColor = .systemRed
            noticeLabel.isHidden = false
        } else {
            noticeLabel.isHidden = true
        }

        guard let view else {
            metadataLabel.stringValue = "读取中"
            addEmptyState("正在读取已核对的日回顾条目…")
            restoreScrollPosition(previousOrigin)
            return
        }

        metadataLabel.stringValue = formattedUpdateTime(view.updatedAt)
        if view.projects.isEmpty {
            addEmptyState("当前没有可恢复的已核对条目。")
        } else {
            for project in view.projects {
                let section = makeProjectSection(
                    project: project,
                    isExpanded: project.key == expandedProjectKey,
                    activeActionIDs: activeActionIDs
                )
                contentStack.addArrangedSubview(section)
                section.leadingAnchor.constraint(equalTo: contentStack.leadingAnchor).isActive = true
                section.trailingAnchor.constraint(equalTo: contentStack.trailingAnchor).isActive = true
            }
        }
        restoreScrollPosition(previousOrigin)
    }

    private func configureWindow() {
        panel.delegate = self
        panel.title = appDisplayName
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isReleasedWhenClosed = false
        // Keep background/header drag in this app so mouse-up is reliably delivered.
        panel.isMovableByWindowBackground = false
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.minSize = NSSize(width: 440, height: 310)
        panel.maxSize = NSSize(width: 460, height: 2000)
        panel.appearance = NSAppearance(named: .aqua)
        panel.isOpaque = true
        panel.backgroundColor = .panelGray
        panel.alphaValue = 1
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true
        moveToUpperRightOfVisibleScreen()
    }

    private func configureContent() {
        guard let contentView = panel.contentView else { return }
        contentView.wantsLayer = true
        contentView.layer?.backgroundColor = NSColor.panelGray.cgColor
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 0
        root.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            root.topAnchor.constraint(equalTo: contentView.topAnchor),
            root.bottomAnchor.constraint(equalTo: contentView.bottomAnchor)
        ])

        headerLabel.font = .systemFont(ofSize: 26, weight: .bold)
        headerLabel.textColor = .panelInk
        metadataLabel.font = .systemFont(ofSize: 18, weight: .bold)
        metadataLabel.textColor = .panelInk
        metadataLabel.alignment = .right
        let reviewLabel = NSTextField(labelWithString: "回顾")
        reviewLabel.font = .systemFont(ofSize: 14)
        reviewLabel.textColor = .projectInk
        let dateStack = NSStackView(views: [metadataLabel, reviewLabel])
        dateStack.orientation = .vertical
        dateStack.alignment = .trailing
        dateStack.spacing = 2
        let spacer = NSView()
        let header = NSStackView(views: [headerLabel, spacer, dateStack])
        header.orientation = .horizontal
        header.alignment = .centerY
        header.spacing = 10
        header.edgeInsets = NSEdgeInsets(top: 16, left: 18, bottom: 14, right: 18)
        root.addArrangedSubview(header)
        pinWidth(header, to: root)
        header.setContentHuggingPriority(.required, for: .vertical)

        noticeLabel.font = .systemFont(ofSize: 12)
        noticeLabel.maximumNumberOfLines = 0
        root.addArrangedSubview(noticeLabel)
        pinWidth(noticeLabel, to: root)

        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        contentStack.spacing = 0
        let documentView = FlippedDocumentView()
        documentView.translatesAutoresizingMaskIntoConstraints = false
        documentView.addSubview(contentStack)
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            contentStack.leadingAnchor.constraint(equalTo: documentView.leadingAnchor),
            contentStack.trailingAnchor.constraint(equalTo: documentView.trailingAnchor),
            contentStack.topAnchor.constraint(equalTo: documentView.topAnchor),
            contentStack.bottomAnchor.constraint(equalTo: documentView.bottomAnchor)
        ])
        scrollView.documentView = documentView
        scrollView.hasHorizontalScroller = false
        scrollView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        scrollView.drawsBackground = false
        scrollView.contentView.drawsBackground = false
        scrollView.hasVerticalScroller = true
        scrollView.scrollerStyle = .overlay
        scrollView.autohidesScrollers = true
        scrollView.borderType = .noBorder
        root.addArrangedSubview(scrollView)
        pinWidth(scrollView, to: root)
        documentView.widthAnchor.constraint(equalTo: scrollView.contentView.widthAnchor).isActive = true

        // Preserve the existing explicit AppKit drag handle.
        let dragHandle = PanelDragHandle()
        dragHandle.translatesAutoresizingMaskIntoConstraints = false
        dragHandle.toolTip = "拖动顶栏，调整浮窗位置"
        dragHandle.setAccessibilityElement(true)
        dragHandle.setAccessibilityRole(.group)
        dragHandle.setAccessibilityLabel("拖动浮窗顶栏")
        contentView.addSubview(dragHandle, positioned: .above, relativeTo: root)
        NSLayoutConstraint.activate([
            dragHandle.leadingAnchor.constraint(equalTo: header.leadingAnchor),
            dragHandle.trailingAnchor.constraint(equalTo: header.trailingAnchor),
            dragHandle.topAnchor.constraint(equalTo: header.topAnchor),
            dragHandle.bottomAnchor.constraint(equalTo: header.bottomAnchor)
        ])
    }

    private func pinWidth(_ view: NSView, to parent: NSView, inset: CGFloat = 0) {
        NSLayoutConstraint.activate([
            view.leadingAnchor.constraint(equalTo: parent.leadingAnchor, constant: inset),
            view.trailingAnchor.constraint(equalTo: parent.trailingAnchor, constant: -inset)
        ])
    }

    private func makeProjectSection(project: PanelProject, isExpanded: Bool, activeActionIDs: Set<String>) -> NSView {
        let section = NSStackView()
        section.orientation = .vertical
        section.alignment = .leading
        section.spacing = 0
        section.setContentHuggingPriority(.required, for: .vertical)
        let heading = ProjectHeadingButton(title: "", target: self, action: #selector(toggleProject(_:)))
        heading.identifier = NSUserInterfaceItemIdentifier(project.key)
        heading.isBordered = false
        heading.wantsLayer = true
        heading.layer?.backgroundColor = NSColor.clear.cgColor
        let parts = project.displayParts
        let displayName = parts.code.isEmpty ? parts.name : parts.code + " · " + parts.name
        let isChatGPT = project.sourceKind == "chatgpt"
        let sourceName = isChatGPT ? "ChatGPT · " : ""
        heading.setAccessibilityLabel("\(sourceName)\(displayName)，\(project.items.count)条，\(isExpanded ? "已展开" : "已折叠")")
        heading.toolTip = sourceName + displayName
        section.addArrangedSubview(heading)
        pinWidth(heading, to: section)
        let symbol = NSImageView(image: NSImage(systemSymbolName: isExpanded ? "chevron.down" : "chevron.right", accessibilityDescription: nil)!
            .withSymbolConfiguration(.init(pointSize: 16, weight: .bold))!)
        symbol.contentTintColor = .projectInk
        let name = NSTextField(wrappingLabelWithString: displayName)
        let styled = NSMutableAttributedString(string: displayName, attributes: [.font: NSFont.systemFont(ofSize: 17, weight: .regular), .foregroundColor: NSColor.projectNameInk])
        if !parts.code.isEmpty {
            styled.addAttributes([.font: NSFont.systemFont(ofSize: 17, weight: .bold), .foregroundColor: NSColor.panelInk], range: NSRange(location: 0, length: (parts.code as NSString).length))
        }
        name.attributedStringValue = styled
        name.maximumNumberOfLines = 0
        name.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        name.setContentCompressionResistancePriority(.required, for: .vertical)
        name.setContentHuggingPriority(.required, for: .horizontal)
        let badge = CountBadge(count: project.items.count)
        for child in [symbol, name, badge] {
            child.translatesAutoresizingMaskIntoConstraints = false
            heading.addSubview(child)
        }
        var nameLeading = name.leadingAnchor.constraint(equalTo: symbol.trailingAnchor, constant: 16)
        if isChatGPT {
            let sourceIcon = NSImageView(image: NSImage(
                systemSymbolName: "bubble.left.and.bubble.right", accessibilityDescription: "ChatGPT 来源"
            )!.withSymbolConfiguration(.init(pointSize: 15, weight: .medium))!)
            sourceIcon.contentTintColor = .projectNameInk
            sourceIcon.identifier = NSUserInterfaceItemIdentifier("chatgpt-source-icon")
            sourceIcon.toolTip = "来自 ChatGPT；无图标的分组来自 Codex"
            sourceIcon.translatesAutoresizingMaskIntoConstraints = false
            heading.addSubview(sourceIcon)
            NSLayoutConstraint.activate([
                sourceIcon.leadingAnchor.constraint(equalTo: symbol.trailingAnchor, constant: 14),
                sourceIcon.centerYAnchor.constraint(equalTo: heading.centerYAnchor),
                sourceIcon.widthAnchor.constraint(equalToConstant: 18),
                sourceIcon.heightAnchor.constraint(equalToConstant: 18)
            ])
            nameLeading = name.leadingAnchor.constraint(equalTo: sourceIcon.trailingAnchor, constant: 8)
        }
        NSLayoutConstraint.activate([
            heading.heightAnchor.constraint(greaterThanOrEqualToConstant: 52),
            symbol.leadingAnchor.constraint(equalTo: heading.leadingAnchor, constant: 20),
            symbol.centerYAnchor.constraint(equalTo: heading.centerYAnchor),
            symbol.widthAnchor.constraint(equalToConstant: 18), symbol.heightAnchor.constraint(equalToConstant: 20),
            nameLeading,
            name.topAnchor.constraint(equalTo: heading.topAnchor, constant: 15),
            name.bottomAnchor.constraint(equalTo: heading.bottomAnchor, constant: -15),
            badge.leadingAnchor.constraint(equalTo: name.trailingAnchor, constant: 5),
            badge.topAnchor.constraint(equalTo: name.topAnchor, constant: -6),
            badge.trailingAnchor.constraint(lessThanOrEqualTo: heading.trailingAnchor, constant: -16),
            badge.widthAnchor.constraint(equalToConstant: badge.diameter),
            badge.heightAnchor.constraint(equalToConstant: badge.diameter)
        ])
        let divider = NSView(); divider.wantsLayer = true
        divider.layer?.backgroundColor = NSColor.panelBorder.cgColor
        divider.heightAnchor.constraint(equalToConstant: 1).isActive = true
        section.addArrangedSubview(divider); pinWidth(divider, to: section)
        guard isExpanded else { return section }
        let cards = NSStackView()
        cards.orientation = .vertical
        cards.alignment = .leading
        cards.spacing = 10
        cards.edgeInsets = NSEdgeInsets(top: 14, left: 14, bottom: 14, right: 14)
        cards.wantsLayer = true
        cards.layer?.backgroundColor = NSColor.clear.cgColor
        section.addArrangedSubview(cards); pinWidth(cards, to: section)
        for item in project.items {
            let card = makeItemRow(item, activeActionIDs: activeActionIDs)
            cards.addArrangedSubview(card); pinWidth(card, to: cards, inset: 14)
        }
        return section
    }

    private func makeItemRow(_ item: PanelItem, activeActionIDs: Set<String>) -> NSView {
        let card = NSView()
        card.identifier = NSUserInterfaceItemIdentifier("card:" + item.id)
        card.wantsLayer = true
        card.layer?.cornerRadius = 8
        card.layer?.backgroundColor = NSColor.white.cgColor
        card.layer?.borderWidth = 1
        card.layer?.borderColor = NSColor.panelBorder.cgColor
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 10
        row.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(row)
        NSLayoutConstraint.activate([
            row.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 12),
            row.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -12),
            row.topAnchor.constraint(equalTo: card.topAnchor, constant: 12),
            row.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -12)
        ])
        let isBusy = activeActionIDs.contains(item.id)
        let textStack = NSStackView()
        textStack.orientation = .vertical
        textStack.alignment = .leading
        textStack.spacing = 4
        textStack.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let title = WrappingRecoveryTitleButton(title: item.title, target: self, action: #selector(navigateToTask(_:)))
        title.item = item
        title.isBordered = false
        title.bezelStyle = .inline
        title.font = .systemFont(ofSize: 16, weight: .semibold)
        title.contentTintColor = .panelInk
        title.alignment = .left
        title.lineBreakMode = .byWordWrapping
        title.cell?.wraps = true
        title.cell?.usesSingleLineMode = false
        let openDescription = item.navigation?.kind == "chatgpt" ? "打开 ChatGPT 原对话（网页）" : "打开对应的原任务"
        title.toolTip = "\(item.title)\n\(openDescription)"
        title.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        title.setContentCompressionResistancePriority(.required, for: .vertical)
        textStack.addArrangedSubview(title); pinWidth(title, to: textStack)
        let point = NSTextField(wrappingLabelWithString: item.listStopLine())
        point.identifier = NSUserInterfaceItemIdentifier("review-point")
        point.font = .systemFont(ofSize: 14.5)
        point.textColor = NSColor(srgbRed: 0.27, green: 0.33, blue: 0.40, alpha: 1)
        // The compact card always exposes one concrete stop. The full verified
        // text remains in its accessible resume description and hover card.
        point.maximumNumberOfLines = 1
        point.lineBreakMode = .byTruncatingTail
        point.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        point.setContentCompressionResistancePriority(.required, for: .vertical)
        textStack.addArrangedSubview(point); pinWidth(point, to: textStack)
        if !item.actionable || isBusy {
            let status = NSTextField(wrappingLabelWithString: isBusy ? "正在提交…" : "内容有更新，等待回顾核对")
            status.font = .systemFont(ofSize: 12)
            status.textColor = .secondaryLabelColor
            textStack.addArrangedSubview(status); pinWidth(status, to: textStack)
        }
        let take = makeActionButton(item, action: .take, enabled: item.actionable && !isBusy)
        let dismiss = makeActionButton(item, action: .dismiss, enabled: item.actionable && !isBusy)
        row.addArrangedSubview(take)
        row.addArrangedSubview(textStack)
        row.addArrangedSubview(dismiss)
        // NSStackView otherwise leaves trailing slack when both text lines are short.
        // Anchor both actions to the card and give all remaining width to the text.
        NSLayoutConstraint.activate([
            take.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 12),
            textStack.leadingAnchor.constraint(equalTo: take.trailingAnchor, constant: 10),
            textStack.trailingAnchor.constraint(equalTo: dismiss.leadingAnchor, constant: -10),
            dismiss.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -12)
        ])
        // This transparent click target covers only the middle text region,
        // including whitespace and padding, without covering either action button.
        let textRegion = TextRegionButton(title: "", target: self, action: #selector(navigateToTask(_:)))
        textRegion.item = item
        textRegion.identifier = NSUserInterfaceItemIdentifier("text-region")
        textRegion.isBordered = false
        textRegion.focusRingType = .none
        let copy = item.resumeCardCopy()
        textRegion.setAccessibilityLabel((item.navigation?.kind == "chatgpt" ? "打开 ChatGPT 原对话：" : "打开对话：") + item.title + "。接续说明")
        textRegion.setAccessibilityHelp(copy.accessibilityText)
        textRegion.onHoverEntered = { [weak self] source in self?.resumeHover.sourcePointerEntered(source) }
        textRegion.onHoverExited = { [weak self] source in self?.resumeHover.sourcePointerExited(source) }
        textRegion.onKeyboardFocus = { [weak self] source in self?.resumeHover.sourcePointerEntered(source) }
        textRegion.onKeyboardBlur = { [weak self] source in self?.resumeHover.sourcePointerExited(source) }
        textRegion.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(textRegion, positioned: .above, relativeTo: row)
        NSLayoutConstraint.activate([
            textRegion.leadingAnchor.constraint(equalTo: textStack.leadingAnchor),
            textRegion.trailingAnchor.constraint(equalTo: textStack.trailingAnchor),
            textRegion.topAnchor.constraint(equalTo: card.topAnchor),
            textRegion.bottomAnchor.constraint(equalTo: card.bottomAnchor)
        ])
        return card
    }

    private func makeActionButton(_ item: PanelItem, action: PanelAction, enabled: Bool) -> RecoveryItemButton {
        let take = action == .take
        let button = RecoveryItemButton(title: "", target: self, action: take ? #selector(takeItem(_:)) : #selector(dismissItem(_:)))
        button.item = item
        button.isBordered = false
        button.image = NSImage(systemSymbolName: take ? "checkmark" : "xmark", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 17, weight: .bold))
        button.imagePosition = .imageOnly
        button.contentTintColor = take ? NSColor(srgbRed: 0.03, green: 0.49, blue: 0.18, alpha: 1) : .projectInk
        button.wantsLayer = true
        button.layer?.cornerRadius = 15
        button.layer?.backgroundColor = (take ? NSColor(srgbRed: 0.81, green: 0.96, blue: 0.88, alpha: 1) : NSColor(srgbRed: 0.91, green: 0.92, blue: 0.94, alpha: 1)).cgColor
        button.layer?.borderWidth = 0.7
        button.layer?.borderColor = (take ? NSColor.systemGreen.withAlphaComponent(0.35) : .panelBorder).cgColor
        button.isEnabled = enabled
        button.alphaValue = enabled ? 1 : 0.45
        button.toolTip = take ? "已接手/有推进，本轮先收起；不是任务完成" : "确认后不再关注此对话，原对话和内容保留"
        button.setAccessibilityLabel(take ? "已推进：" + item.title : "不再关注：" + item.title)
        NSLayoutConstraint.activate([button.widthAnchor.constraint(equalToConstant: 30), button.heightAnchor.constraint(equalToConstant: 30)])
        return button
    }

    // Render only this app's own view hierarchy, without reading any other window.
    func saveOwnPreview(to url: URL) {
        guard let view = panel.contentView else { return }
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        if let data = bitmap.representation(using: .png, properties: [:]) { try? data.write(to: url, options: .atomic) }
    }

    private func addEmptyState(_ text: String) {
        let label = NSTextField(wrappingLabelWithString: text)
        label.textColor = .secondaryLabelColor
        label.font = .systemFont(ofSize: 14)
        contentStack.addArrangedSubview(label)
        label.leadingAnchor.constraint(equalTo: contentStack.leadingAnchor).isActive = true
        label.trailingAnchor.constraint(equalTo: contentStack.trailingAnchor).isActive = true
    }

    private func restoreScrollPosition(_ previousOrigin: NSPoint) {
        panel.contentView?.layoutSubtreeIfNeeded()
        let documentHeight = scrollView.documentView?.frame.height ?? 0
        let viewportHeight = scrollView.contentView.bounds.height
        let maximumY = max(0, documentHeight - viewportHeight)
        let origin = NSPoint(x: 0, y: min(max(0, previousOrigin.y), maximumY))
        scrollView.contentView.scroll(to: origin)
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    private func formattedUpdateTime(_ value: String?) -> String {
        guard let value,
              let date = fractionalISO8601Formatter.date(from: value) ?? standardISO8601Formatter.date(from: value) else {
            return "等待首次回顾"
        }
        return headerDateFormatter.string(from: date)
    }

    private func moveToUpperRightOfVisibleScreen() {
        guard let screen = NSScreen.main ?? NSScreen.screens.first else { return }
        let visible = screen.visibleFrame
        let margin: CGFloat = 18
        let frame = NSRect(
            x: visible.maxX - panel.frame.width - margin,
            y: visible.maxY - panel.frame.height - margin,
            width: panel.frame.width,
            height: panel.frame.height
        )
        panel.setFrame(clampedFrame(frame, within: visible), display: false)
    }

    private func clampedFrame(_ frame: NSRect, within visibleFrame: NSRect) -> NSRect {
        let width = min(max(frame.width, panel.minSize.width), panel.maxSize.width, visibleFrame.width)
        let height = min(max(frame.height, panel.minSize.height), visibleFrame.height)
        let x = min(max(frame.origin.x, visibleFrame.minX), visibleFrame.maxX - width)
        let y = min(max(frame.origin.y, visibleFrame.minY), visibleFrame.maxY - height)
        return NSRect(x: x, y: y, width: width, height: height)
    }

    @objc private func toggleProject(_ sender: NSButton) {
        clearResumeHover()
        guard let key = sender.identifier?.rawValue else { return }
        onToggleProject?(key)
    }

    @objc private func navigateToTask(_ sender: RecoveryItemButton) {
        clearResumeHover()
        guard let item = sender.item else { return }
        onNavigate?(item)
    }

    @objc private func takeItem(_ sender: RecoveryItemButton) {
        clearResumeHover()
        guard let item = sender.item else { return }
        onAction?(item, .take)
    }

    @objc private func dismissItem(_ sender: RecoveryItemButton) {
        clearResumeHover()
        guard let item = sender.item else { return }
        onAction?(item, .dismiss)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { false }

    func windowDidMove(_ notification: Notification) {
        clearResumeHover()
        onWindowMovedByUser?(panel.frame)
    }
}

private struct BadgeDiagnostics: Codable {
    let count: Int
    let frame: SavedFrame
    let ink: SavedFrame
}

private struct CardLayoutDiagnostics: Codable {
    let id: String
    let textRegion: SavedFrame
    let cardAlpha: CGFloat
    let titleColorAlpha: CGFloat
    let bodyColorAlpha: CGFloat
    let frame: SavedFrame
    let titleHeight: CGFloat
    let titleRequiredHeight: CGFloat
    let bodyHeight: CGFloat
    let bodyRequiredHeight: CGFloat
    let title: String
    let reviewPoint: String
}

private struct WindowLayoutDiagnostics: Codable {
    let window: SavedFrame
    let visibleScreen: SavedFrame
    let viewportWidth: CGFloat
    let documentWidth: CGFloat
    let actionButtons: [SavedFrame]
    let horizontalContentFits: Bool
    let windowFitsScreen: Bool
    let reviewDate: String
    let projectHeadings: [String]
    let badges: [BadgeDiagnostics]
    let outerBackgroundAlpha: CGFloat
    let windowAlpha: CGFloat
    let opaqueWindow: Bool
    let dragPhase: String
    let projectNameColor: String
    let cards: [CardLayoutDiagnostics]
}

private struct TestInteractionEvent: Codable {
    let event: String
    let id: String
}

private struct DiagnosticsSnapshot: Codable {
    let visible: Bool
    let backendLoaded: Bool
    let backendRevision: String?
    let appVersion: String
    let testMode: Bool
    let processID: Int32
    let expandedProject: String?
    let projectCount: Int
    let itemCount: Int
    let windowLevel: String
    let collectionBehavior: [String]
    let layout: WindowLayoutDiagnostics
    let confirmationVisible: Bool
    let testEvents: [TestInteractionEvent]

    enum CodingKeys: String, CodingKey {
        case visible
        case backendLoaded = "backend_loaded"
        case backendRevision = "backend_revision"
        case appVersion = "app_version"
        case testMode = "test_mode"
        case processID = "process_id"
        case expandedProject = "expanded_project"
        case projectCount = "project_count"
        case itemCount = "item_count"
        case windowLevel = "window_level"
        case collectionBehavior = "collection_behavior"
        case layout
        case confirmationVisible = "confirmation_visible"
        case testEvents = "test_events"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(visible, forKey: .visible)
        try container.encode(backendLoaded, forKey: .backendLoaded)
        try container.encodeIfPresent(backendRevision, forKey: .backendRevision)
        try container.encode(appVersion, forKey: .appVersion)
        try container.encode(testMode, forKey: .testMode)
        try container.encode(processID, forKey: .processID)
        if let expandedProject {
            try container.encode(expandedProject, forKey: .expandedProject)
        } else {
            try container.encodeNil(forKey: .expandedProject)
        }
        try container.encode(projectCount, forKey: .projectCount)
        try container.encode(itemCount, forKey: .itemCount)
        try container.encode(windowLevel, forKey: .windowLevel)
        try container.encode(collectionBehavior, forKey: .collectionBehavior)
        try container.encode(layout, forKey: .layout)
        try container.encode(confirmationVisible, forKey: .confirmationVisible)
        try container.encode(testEvents, forKey: .testEvents)
    }
}

private final class FloatingPanelDelegate: NSObject, NSApplicationDelegate {
    private let configuration: AppConfiguration
    private let backend: RecoveryBackend
    private let uiStateStore: PanelUIStateStore
    private let windowController = PanelWindowController()
    private let lifecycleWorkspace = NSWorkspace.shared
    private let watcherQueue = DispatchQueue(label: "ResumeDesk.RecoveryFloatingPanel.state-watcher")

    private var workspaceObservers: [NSObjectProtocol] = []
    private var statusItem: NSStatusItem?
    private var stateWatcher: DispatchSourceFileSystemObject?
    private var debounceWorkItem: DispatchWorkItem?
    private var panelView: PanelView?
    private var expandedProjectKey: String?
    private var activeActionIDs = Set<String>()
    private var transientMessage: String?
    private var lastAppliedRevision: String?
    private var codexIsRunning = false
    private var hasLoadedUIState = false
    private var testEvents: [TestInteractionEvent] = []

    init(configuration: AppConfiguration) {
        self.configuration = configuration
        backend = RecoveryBackend(configuration: configuration)
        uiStateStore = PanelUIStateStore(stateDirectoryURL: configuration.stateDirectoryURL)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let savedUIState = uiStateStore.load()
        // A new launch opens the newest pending project. The selected project is retained only during this
        // running session and across ordinary panel data refreshes; only the window placement is restored.
        expandedProjectKey = nil
        windowController.restoreWindowFrame(savedUIState?.frame)
        hasLoadedUIState = true
        saveUIState()
        configureWindowCallbacks()
        configureStatusItem()
        observeWorkspaceLifecycle()
        installDirectoryWatcher()
        updateTargetApplicationState()
        refreshPanel(forceApply: true)
    }

    func applicationDidResignActive(_ notification: Notification) {
        windowController.cancelDrag()
        windowController.clearResumeHover()
    }

    func applicationWillTerminate(_ notification: Notification) {
        windowController.cancelDrag()
        windowController.clearResumeHover()
        debounceWorkItem?.cancel()
        stateWatcher?.cancel()
        workspaceObservers.forEach { lifecycleWorkspace.notificationCenter.removeObserver($0) }
    }

    private func configureWindowCallbacks() {
        windowController.onToggleProject = { [weak self] key in
            guard let self else { return }
            expandedProjectKey = key
            saveUIState()
            render()
        }
        windowController.onWindowMovedByUser = { [weak self] _ in
            self?.saveUIState()
            self?.writeDiagnostics()
        }
        windowController.onNavigate = { [weak self] item in
            self?.navigateToOriginalTask(item)
        }
        windowController.onAction = { [weak self] item, action in
            guard let self else { return }
            if action == .dismiss {
                guard item.actionable, !activeActionIDs.contains(item.id), !windowController.confirmationVisible else { return }
                windowController.confirmDismiss(item) { [weak self] confirmed in
                    guard let self else { return }
                    recordTestEvent(confirmed ? "dismiss_confirmed" : "dismiss_cancelled", item: item)
                    if confirmed { performAction(item, action: .dismiss) }
                    else { writeDiagnostics() }
                }
                recordTestEvent("dismiss_prompt", item: item)
                writeDiagnostics()
            } else { performAction(item, action: action) }
        }
    }

    private func configureStatusItem() {
        let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let image = NSImage(systemSymbolName: "rectangle.on.rectangle", accessibilityDescription: appDisplayName)
        image?.isTemplate = true
        statusItem.button?.image = image
        statusItem.button?.toolTip = appDisplayName

        let menu = NSMenu()
        let quit = NSMenuItem(title: "退出浮窗", action: #selector(quitFloatingPanel(_:)), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu
        self.statusItem = statusItem
    }

    private func observeWorkspaceLifecycle() {
        let center = lifecycleWorkspace.notificationCenter
        let launched = center.addObserver(forName: NSWorkspace.didLaunchApplicationNotification, object: nil, queue: .main) { [weak self] _ in
            self?.updateTargetApplicationState()
        }
        let terminated = center.addObserver(forName: NSWorkspace.didTerminateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
            self?.updateTargetApplicationState()
        }
        workspaceObservers = [launched, terminated]
    }

    private func updateTargetApplicationState() {
        let wasRunning = codexIsRunning
        if let mockedRunning = configuration.testCodexRunning {
            codexIsRunning = mockedRunning
        } else {
            codexIsRunning = lifecycleWorkspace.runningApplications.contains { application in
                application.bundleIdentifier == configuration.codexBundleIdentifier
            }
        }
        if codexIsRunning {
            if !wasRunning, let view = panelView {
                // A new Codex run is a new entry into the recovery list. Ordinary
                // app switches and daily data refreshes preserve the reading choice.
                expandedProjectKey = latestProjectKey(in: view.projects)
                windowController.scrollToTop()
                render()
            }
            windowController.showWithoutActivation()
        } else {
            windowController.hide()
        }
        writeDiagnostics()
    }

    private func installDirectoryWatcher() {
        let descriptor = open(configuration.stateDirectoryURL.path, O_EVTONLY)
        guard descriptor >= 0 else {
            transientMessage = "无法监听运行数据目录；浮窗仍会在启动和操作后读取队列。"
            render()
            return
        }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: descriptor,
            eventMask: [.write, .extend, .attrib, .link, .rename, .delete],
            queue: watcherQueue
        )
        source.setEventHandler { [weak self] in
            self?.scheduleDirectoryRefresh()
        }
        source.setCancelHandler {
            close(descriptor)
        }
        stateWatcher = source
        source.resume()
    }

    private func scheduleDirectoryRefresh() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            debounceWorkItem?.cancel()
            let work = DispatchWorkItem { [weak self] in
                self?.refreshPanel(forceApply: false)
            }
            debounceWorkItem = work
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(260), execute: work)
        }
    }

    private func refreshPanel(forceApply: Bool) {
        // A read can replace an item with the same visible revision in an
        // external state transition. Never leave an old hover card around it.
        windowController.clearResumeHover()
        backend.read { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let updatedView):
                transientMessage = nil
                let dataChanged = updatedView.revision != lastAppliedRevision
                panelView = updatedView
                selectExpandedProjectIfNeeded(in: updatedView)
                if forceApply || dataChanged {
                    lastAppliedRevision = updatedView.revision
                    render()
                }
            case .failure(let error):
                transientMessage = error.localizedDescription
                render()
            }
        }
    }

    private func selectExpandedProjectIfNeeded(in view: PanelView) {
        if let expandedProjectKey, view.projects.contains(where: { $0.key == expandedProjectKey }) {
            return
        }
        expandedProjectKey = latestProjectKey(in: view.projects)
    }

    private func latestProjectKey(in projects: [PanelProject]) -> String? {
        projects.max { left, right in
            latestTimestamp(in: left) < latestTimestamp(in: right)
        }?.key
    }

    private func latestTimestamp(in project: PanelProject) -> String {
        project.updatedAt ?? ""
    }

    private func performAction(_ item: PanelItem, action: PanelAction) {
        guard item.actionable, !activeActionIDs.contains(item.id) else { return }
        windowController.clearResumeHover()
        activeActionIDs.insert(item.id)
        recordTestEvent("submit_" + action.rawValue, item: item)
        transientMessage = nil
        render()
        backend.perform(item: item, action: action) { [weak self] result in
            guard let self else { return }
            activeActionIDs.remove(item.id)
            switch result {
            case .success(let updatedView):
                panelView = updatedView
                lastAppliedRevision = updatedView.revision
                transientMessage = nil
                selectExpandedProjectIfNeeded(in: updatedView)
                render()
            case .failure(let error):
                recordTestEvent("action_rejected", item: item)
                // Do not optimistically remove this row. Retain it until the authoritative read says otherwise.
                transientMessage = "未能\(action.displayName)：\(error.localizedDescription)；正在重新读取。"
                render()
                refreshPanel(forceApply: true)
            }
        }
    }

    private func navigateToOriginalTask(_ item: PanelItem) {
        guard let url = item.navigation?.destination(for: item.id) else {
            transientMessage = "这条记录缺少有效的原任务入口，请等待回顾重新核对。"
            render()
            return
        }
        guard !configuration.testMode else {
            recordTestEvent("navigation_intercepted", item: item)
            writeDiagnostics()
            return
        }
        if !NSWorkspace.shared.open(url) {
            transientMessage = "无法打开原任务；恢复队列没有被修改。"
            render()
        }
    }

    private func recordTestEvent(_ event: String, item: PanelItem) {
        guard configuration.testMode else { return }
        testEvents.append(TestInteractionEvent(event: event, id: item.id))
    }

    private func saveUIState() {
        guard hasLoadedUIState else { return }
        uiStateStore.save(frame: windowController.frame)
    }

    private func render() {
        windowController.render(
            view: panelView,
            expandedProjectKey: expandedProjectKey,
            activeActionIDs: activeActionIDs,
            transientMessage: transientMessage
        )
        writeDiagnostics()
        if configuration.diagnosticsURL != nil {
            // AppKit commits NSClipView/document geometry at the end of this run loop.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in self?.writeDiagnostics() }
        }
    }

    private func writeDiagnostics() {
        guard let diagnosticsURL = configuration.diagnosticsURL else { return }
        let view = panelView
        let snapshot = DiagnosticsSnapshot(
            visible: windowController.isVisible,
            backendLoaded: view != nil,
            backendRevision: view?.revision,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown",
            testMode: configuration.testMode,
            processID: ProcessInfo.processInfo.processIdentifier,
            expandedProject: expandedProjectKey,
            projectCount: view?.projects.count ?? 0,
            itemCount: view?.projects.reduce(0) { $0 + $1.items.count } ?? 0,
            windowLevel: windowController.levelName,
            collectionBehavior: windowController.collectionBehaviorNames,
            layout: windowController.layoutDiagnostics,
            confirmationVisible: windowController.confirmationVisible,
            testEvents: testEvents
        )
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(snapshot).write(to: diagnosticsURL, options: .atomic)
            if configuration.testMode {
                windowController.saveOwnPreview(to: diagnosticsURL.deletingPathExtension().appendingPathExtension("png"))
            }
        } catch {
            NSLog("%@：无法写入诊断信息：%@", appDisplayName, error.localizedDescription)
        }
    }

    @objc private func quitFloatingPanel(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}
