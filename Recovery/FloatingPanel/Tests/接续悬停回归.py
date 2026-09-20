#!/usr/bin/env python3
"""Replay the resume-hover lifecycle in an isolated AppKit bundle.

This test compiles the actual panel source with a temporary test main. It does
not start the checked-in app, read production state, navigate a Codex task, or
submit a panel action.
"""
from pathlib import Path
import json
import subprocess
import tempfile


APP = Path(__file__).resolve().parents[1]

TEST_MAIN = r'''
private extension PanelWindowController {
    func verifyResumeHover() -> [[String: Any]] {
        var trace: [[String: Any]] = []
        func stage(_ value: String) {
            FileHandle.standardError.write(Data(("resume-hover-stage: " + value + "\\n").utf8))
        }
        panel.setFrame(NSRect(x: 980, y: 150, width: 452, height: 620), display: true)
        panel.orderFrontRegardless()
        defer { clearResumeHover(); panel.orderOut(nil) }

        func descendants(_ view: NSView) -> [NSView] {
            view.subviews.flatMap { [$0] + descendants($0) }
        }
        func wait(_ seconds: TimeInterval) {
            RunLoop.current.run(until: Date(timeIntervalSinceNow: seconds))
        }
        func pointerEvent() -> NSEvent {
            NSEvent.mouseEvent(
                with: .mouseMoved,
                location: NSPoint(x: 10, y: 10),
                modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: panel.windowNumber,
                context: nil,
                eventNumber: 1,
                clickCount: 0,
                pressure: 0
            )!
        }
        func source() -> TextRegionButton {
            panel.contentView?.layoutSubtreeIfNeeded()
            return descendants(contentStack).compactMap { $0 as? TextRegionButton }.first!
        }
        func visibleStopLine() -> String {
            descendants(contentStack).compactMap { $0 as? NSTextField }
                .first { $0.identifier?.rawValue == "review-point" }!.stringValue
        }
        func show(_ source: TextRegionButton) {
            source.mouseEntered(with: pointerEvent())
            wait(0.18)
            precondition(!resumeHover.isVisible, "hover appeared before the 400ms dwell")
            wait(0.31)
            precondition(resumeHover.isVisible, "hover did not appear after dwell")
        }

        let richJSON = #"{"schema_version":1,"revision":"rich","updated_at":null,"notice":"","projects":[{"key":"qa","label":"QA","updated_at":null,"items":[{"id":"fa000000-0000-4000-8000-000000000001","title":"原对话标题","review_point":"审核最新交接单","fingerprint":"rich","action_token":"fake","actionable":true,"resume_context":{"previous_focus":"核对交接单的来源","current_state":"已完成字段逐条核对","next_step":"回到原对话确认最后一项"}}]}]}"#
        let legacyJSON = #"{"schema_version":1,"revision":"legacy","updated_at":null,"notice":"","projects":[{"key":"qa","label":"QA","updated_at":null,"items":[{"id":"fa000000-0000-4000-8000-000000000001","title":"原对话标题","review_point":"审核最新交接单","fingerprint":"legacy","action_token":"fake","actionable":true}]}]}"#
        let inactiveJSON = #"{"schema_version":1,"revision":"inactive","updated_at":null,"notice":"","projects":[{"key":"qa","label":"QA","updated_at":null,"items":[{"id":"fa000000-0000-4000-8000-000000000001","title":"原对话标题","review_point":"等待回顾核对的旧停点","fingerprint":"inactive","action_token":"fake","actionable":false,"resume_context":{"previous_focus":"不应显示的旧目标","current_state":"不应显示的旧当前状态","next_step":"不应显示的旧下一步"}}]}]}"#
        let replacedJSON = #"{"schema_version":1,"revision":"replaced","updated_at":null,"notice":"","projects":[{"key":"qa","label":"QA","updated_at":null,"items":[{"id":"fa000000-0000-4000-8000-000000000001","title":"更新后的原对话标题","review_point":"确认新版字段","fingerprint":"replaced","action_token":"fake","actionable":true,"resume_context":{"previous_focus":"比较新旧字段","current_state":"新版已到待确认","next_step":"打开原对话决定是否接手"}}]}]}"#
        let rich = try! JSONDecoder().decode(PanelView.self, from: Data(richJSON.utf8))
        let legacy = try! JSONDecoder().decode(PanelView.self, from: Data(legacyJSON.utf8))
        let inactive = try! JSONDecoder().decode(PanelView.self, from: Data(inactiveJSON.utf8))
        let replaced = try! JSONDecoder().decode(PanelView.self, from: Data(replacedJSON.utf8))

        let targetID = "fa000000-0000-4000-8000-000000000001"
        precondition(PanelNavigation(kind: "chatgpt", url: "https://chatgpt.com/c/" + targetID).destination(for: targetID)?.scheme == "https")
        precondition(PanelNavigation(kind: "codex", url: "codex://threads/" + targetID).destination(for: targetID)?.scheme == "codex")
        precondition(PanelNavigation(kind: "chatgpt", url: "codex://threads/" + targetID).destination(for: targetID) == nil)
        precondition(PanelNavigation(kind: "chatgpt", url: "https://chatgpt.com.evil.invalid/c/" + targetID).destination(for: targetID) == nil)
        precondition(PanelNavigation(kind: "chatgpt", url: "https://chatgpt.com/c/" + targetID + "?prompt=run").destination(for: targetID) == nil)
        trace.append(["check": "source navigation selects original route and rejects mismatches", "passed": true])

        var navigations = 0
        var actions: [String] = []
        onNavigate = { _ in navigations += 1 }
        onAction = { _, action in actions.append(action.rawValue) }
        stage("rich-render")
        render(view: rich, expandedProjectKey: "qa", activeActionIDs: [], transientMessage: nil)
        precondition(visibleStopLine() == "已完成字段逐条核对", "list did not prefer verified current state")
        precondition(!descendants(contentStack).contains { $0.identifier?.rawValue == "chatgpt-source-icon" })
        let richSource = source()
        precondition(richSource.toolTip == nil, "custom card must not duplicate a system tooltip")
        precondition(richSource.accessibilityHelp()?.contains("上次在做：核对交接单的来源") == true)
        richSource.performClick(nil)
        precondition(navigations == 1 && actions.isEmpty, "text navigation changed")
        stage("rich-show")
        show(richSource)
        precondition(resumeHover.displayedCopy == ResumeCardCopy(
            previousFocus: "核对交接单的来源",
            currentState: "已完成字段逐条核对",
            nextStep: "回到原对话确认最后一项"
        ))
        precondition(!resumeHover.canBecomeKey && !resumeHover.isKeyWindow, "hover stole keyboard focus")
        trace.append(["check": "400ms dwell and three verified lines", "visible": resumeHover.isVisible])

        // Leaving the source and entering the exterior card before its grace
        // timer expires keeps the same card readable.
        richSource.mouseExited(with: pointerEvent())
        resumeHover.cardPointerEntered()
        stage("card-transition")
        wait(0.23)
        precondition(resumeHover.isVisible, "card closed while pointer was on it")
        resumeHover.cardPointerExited()
        wait(0.45)
        precondition(!resumeHover.isVisible, "card stayed after leaving source and card")
        trace.append(["check": "source-to-card transition and exit", "visible_after_exit": resumeHover.isVisible])

        // Legacy rows decode without the new field. review_point is shown only
        // as a pending-recheck current stop; no former goal or next step is invented.
        render(view: legacy, expandedProjectKey: "qa", activeActionIDs: [], transientMessage: nil)
        let legacySource = source()
        stage("legacy-show")
        show(legacySource)
        precondition(resumeHover.displayedCopy == ResumeCardCopy(
            previousFocus: "尚未核实",
            currentState: "审核最新交接单（沿用原停点，等待核对）",
            nextStep: "尚未核实"
        ))
        trace.append(["check": "legacy fallback is explicit", "current": resumeHover.displayedCopy!.currentState])
        let inactiveItem = inactive.projects[0].items[0]
        precondition(inactiveItem.listStopLine() == "等待回顾核对的旧停点")
        precondition(inactiveItem.resumeCardCopy() == ResumeCardCopy(
            previousFocus: "尚未核实",
            currentState: "等待回顾核对的旧停点（沿用原停点，等待核对）",
            nextStep: "尚未核实"
        ), "inactive context was shown as current")
        trace.append(["check": "inactive row does not surface stale resume context", "current": inactiveItem.resumeCardCopy().currentState])

        // Every replacement path clears the exterior window before replacing a
        // source button, preventing stale copy from floating beside fresh rows.
        render(view: replaced, expandedProjectKey: "qa", activeActionIDs: [], transientMessage: nil)
        precondition(!resumeHover.isVisible && resumeHover.displayedCopy == nil, "refresh retained stale hover")
        let replacedSource = source()
        stage("replaced-show")
        show(replacedSource)
        precondition(resumeHover.displayedCopy?.currentState == "新版已到待确认")
        windowDidMove(Notification(name: NSWindow.didMoveNotification, object: panel))
        precondition(!resumeHover.isVisible, "drag/move retained hover")
        stage("collapse")
        show(source())
        render(view: replaced, expandedProjectKey: nil, activeActionIDs: [], transientMessage: nil)
        precondition(!resumeHover.isVisible, "collapse retained hover")
        render(view: replaced, expandedProjectKey: "qa", activeActionIDs: [], transientMessage: nil)
        stage("hide")
        show(source())
        hide()
        precondition(!resumeHover.isVisible, "hide retained hover")
        panel.orderFrontRegardless()
        trace.append(["check": "refresh, drag, collapse, hide clear stale card", "visible": resumeHover.isVisible])

        let cloudJSON = richJSON.replacingOccurrences(of: "\"key\":\"qa\",\"label\":\"QA\"", with: "\"key\":\"qa\",\"label\":\"独立对话\",\"source_kind\":\"chatgpt\"")
        let cloud = try! JSONDecoder().decode(PanelView.self, from: Data(cloudJSON.utf8))
        render(view: cloud, expandedProjectKey: "qa", activeActionIDs: [], transientMessage: nil)
        precondition(descendants(contentStack).filter { $0.identifier?.rawValue == "chatgpt-source-icon" }.count == 1)
        trace.append(["check": "ChatGPT group has source icon; Codex group has none", "passed": true])

        let sample = ResumeHoverCardView(frame: .zero)
        let samplePanel = ResumeHoverPanel()
        samplePanel.contentView = sample
        _ = sample.configure(copy: ResumeCardCopy(
            previousFocus: "把公开 GitHub 介绍改成用户容易理解的产品表达。",
            currentState: "顶部简介和 Release 已更新；README 与对应文档检查已准备，原任务仍在等推送确认。",
            nextStep: "回到原任务确认这次推送范围，由该任务接着处理。"
        ), projectLabel: "061 · 示例项目")
        let sampleSize = sample.displaySize(maximumHeight: 480)
        samplePanel.setContentSize(sampleSize)
        samplePanel.orderFrontRegardless()
        sample.layoutSubtreeIfNeeded()
        sample.displayIfNeeded()
        let bodyFields = descendants(sample).compactMap { $0 as? NSTextField }
            .filter { $0.identifier?.rawValue.hasPrefix("resume-") == true }
        precondition(bodyFields.count == 3)
        precondition(bodyFields.allSatisfy { field in
            let need = field.cell!.cellSize(forBounds: NSRect(x: 0, y: 0, width: field.frame.width, height: 10000))
            return need.height <= field.frame.height + 1
        }, "native text field content clips")
        if let output = ProcessInfo.processInfo.environment["RESUME_DESK_CARD_QA_OUTPUT"],
           let bitmap = sample.bitmapImageRepForCachingDisplay(in: sample.bounds) {
            sample.cacheDisplay(in: sample.bounds, to: bitmap)
            try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
        }
        samplePanel.orderOut(nil)
        trace.append(["check": "three separated sections fit real mixed Chinese English copy", "width": sampleSize.width, "height": sampleSize.height, "body_sections": bodyFields.count])

        // Verify the pure exterior-placement calculation independently of the
        // host screen. The card stays visible and does not overlap the host (or
        // its action controls) when either side has room.
        let host = NSRect(x: 980, y: 150, width: 452, height: 620)
        stage("placement")
        let visible = NSRect(x: 0, y: 0, width: 1440, height: 900)
        let card = ResumeHoverPlacement.frame(
            anchor: NSRect(x: 1080, y: 420, width: 220, height: 80),
            host: host,
            cardSize: NSSize(width: 318, height: 150),
            visibleFrame: visible
        )
        precondition(visible.contains(card) && !card.intersects(host), "exterior placement was invalid")
        trace.append(["check": "exterior placement fits screen and avoids host", "x": card.minX, "y": card.minY])

        // A 600-character row must retain its tail in an internal scroller
        // instead of growing beyond a small screen or clipping the third line.
        let long = String(repeating: "核对来源和停点。", count: 75)
        let longCard = ResumeHoverCardView(frame: NSRect(x: 0, y: 0, width: 318, height: 1))
        let longPanel = ResumeHoverPanel()
        longPanel.contentView = longCard
        let natural = longCard.configure(copy: ResumeCardCopy(previousFocus: long, currentState: long, nextStep: long))
        let fitted = longCard.displaySize(maximumHeight: 164)
        longPanel.setContentSize(fitted)
        longCard.layoutSubtreeIfNeeded()
        stage("long geometry natural=\(natural.height) fitted=\(fitted.height) scroll=\(longCard.isScrollable) document=\(longCard.documentTextHeight) viewport=\(longCard.visibleTextHeight)")
        precondition(natural.height > fitted.height && longCard.isScrollable
            && longCard.documentTextHeight > longCard.visibleTextHeight, "long content was clipped instead of scrollable")
        trace.append(["check": "long three-line content scrolls inside visible geometry", "natural_height": natural.height, "shown_height": fitted.height])
        return trace
    }
}

@main
private struct ResumeHoverTests {
    static func main() throws {
        NSApplication.shared.setActivationPolicy(.accessory)
        let controller = PanelWindowController()
        let trace = controller.verifyResumeHover()
        let result: [String: Any] = [
            "passed": true,
            "checks": trace.count,
            "trace": trace,
            "event_entry": "actual TextRegionButton mouse-enter/exit plus AppKit run-loop timers",
            "backend_called": false,
            "production_state_read": false,
            "formal_app_started": false,
            "test_window_closed": true
        ]
        print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
    }
}
'''


def main():
    root = Path(tempfile.mkdtemp(prefix='resume-desk-resume-hover-'))
    source = (APP/'Sources/FloatingRecoveryApp.swift').read_text()
    assert source.count('@main\nstruct FloatingRecoveryApp') == 1
    source = source.replace('@main\nstruct FloatingRecoveryApp', 'struct FloatingRecoveryApp', 1)
    swift = root/'ResumeHoverTests.swift'
    swift.write_text(source + '\n' + TEST_MAIN)
    binary = root/'ResumeHoverTests'
    subprocess.run([
        'xcrun', 'swiftc', str(swift), '-parse-as-library',
        '-framework', 'AppKit', '-framework', 'Foundation',
        '-module-cache-path', str(root/'module-cache'), '-o', str(binary)
    ], check=True)
    import os
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=25,
                            env=dict(os.environ, RESUME_DESK_CARD_QA_OUTPUT=str(root/'resume-card.png')))
    (root/'stderr.log').write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Native test exited {result.returncode}: {result.stderr[-1800:]}')
    parsed = json.loads(result.stdout)
    (root/'result.json').write_text(json.dumps(parsed, ensure_ascii=False, indent=2))
    print(json.dumps({'isolated_binary': str(binary), 'result_path': str(root/'result.json'), 'result': parsed}, ensure_ascii=False))


if __name__ == '__main__':
    main()
