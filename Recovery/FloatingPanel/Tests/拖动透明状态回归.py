#!/usr/bin/env python3
"""Compile the actual app source with an isolated, hidden-window state test main.
This replays local NSEvents through the real title view; it is not physical mouse validation.
"""
from pathlib import Path
import json
import subprocess
import tempfile

APP = Path(__file__).resolve().parents[1]
TEST_MAIN = r'''
private extension PanelWindowController {
    func verifyInputPath() -> [[String: Any]] {
        var trace: [[String: Any]] = []
        panel.setFrame(NSRect(x: 120, y: 120, width: 452, height: 600), display: true)
        panel.orderFrontRegardless()
        defer { panel.finishLocalDrag(); panel.orderOut(nil) }
        func record(_ stage: String, phase: String, alpha: CGFloat) {
            precondition(panel.dragPhase == phase, stage + " wrong phase: " + panel.dragPhase)
            precondition(abs(panel.alphaValue - alpha) < 0.001, stage + " wrong alpha")
            trace.append(["stage":stage, "phase":panel.dragPhase, "alpha":panel.alphaValue,
                "x":panel.frame.minX, "y":panel.frame.minY])
        }
        func pointer(_ type: NSEvent.EventType, screen: NSPoint) -> NSEvent {
            NSEvent.mouseEvent(with:type, location:panel.convertPoint(fromScreen:screen), modifierFlags:[],
                timestamp:ProcessInfo.processInfo.systemUptime, windowNumber:panel.windowNumber,
                context:nil, eventNumber:1, clickCount:1, pressure:0.5)!
        }
        func headerPoint() -> NSPoint { NSPoint(x:panel.frame.minX + 160, y:panel.frame.maxY - 38) }
        func down() -> NSPoint {
            let point = headerPoint()
            panel.sendEvent(pointer(.leftMouseDown, screen:point))
            return point
        }
        func move(_ origin: NSPoint, dx: CGFloat = 45, dy: CGFloat = -25) {
            panel.sendEvent(pointer(.leftMouseDragged, screen:NSPoint(x:origin.x + dx, y:origin.y + dy)))
        }
        func release(_ point: NSPoint) { panel.sendEvent(pointer(.leftMouseUp, screen:point)) }
        record("normal", phase:"idle", alpha:1)
        precondition(panel.backgroundColor.alphaComponent == 1)
        precondition(panel.contentView?.layer?.backgroundColor?.alpha == 1)
        let before = panel.frame.origin
        let start = down()
        record("header mouseDown returns, armed state remains", phase:"armed", alpha:1)
        RunLoop.current.run(until:Date(timeIntervalSinceNow:0.03))
        record("between asynchronous input events", phase:"armed", alpha:1)
        move(start)
        record("mouseDragged moves and fades window", phase:"dragging", alpha:0.5)
        precondition(abs(panel.frame.minX - before.x - 45) < 0.1)
        precondition(abs(panel.frame.minY - before.y + 25) < 0.1)
        RunLoop.current.run(until:Date(timeIntervalSinceNow:0.03))
        record("after drag handler returns, stays faded", phase:"dragging", alpha:0.5)
        move(start, dx:90, dy:-40)
        precondition(abs(panel.frame.minX - before.x - 90) < 0.1)
        precondition(abs(panel.frame.minY - before.y + 40) < 0.1)
        record("second input uses stable screen anchor", phase:"dragging", alpha:0.5)
        release(NSPoint(x:start.x+90,y:start.y-40))
        record("mouseUp restores immediately", phase:"idle", alpha:1)
        let click = down(); release(click)
        record("plain header click remains opaque", phase:"idle", alpha:1)
        let escaped = down(); move(escaped)
        let key = NSEvent.keyEvent(with:.keyDown, location:.zero, modifierFlags:[], timestamp:0,
            windowNumber:panel.windowNumber, context:nil, characters:"\u{1b}", charactersIgnoringModifiers:"\u{1b}",
            isARepeat:false, keyCode:53)!
        panel.sendEvent(key)
        record("Escape input cancels", phase:"idle", alpha:1)
        move(escaped)
        record("late drag event after cancel ignored", phase:"idle", alpha:1)
        let cancel = down(); move(cancel); panel.cancelOperation(nil)
        record("responder cancellation restores", phase:"idle", alpha:1)
        let hidden = down(); move(hidden); hide()
        record("hidden during drag restores", phase:"idle", alpha:1)
        panel.orderFrontRegardless()
        let focus = down(); move(focus); panel.resignKey()
        record("focus interruption restores", phase:"idle", alpha:1)
        let next = down(); move(next)
        record("next drag after interruption works", phase:"dragging", alpha:0.5)
        release(next)
        panel.setFrameOrigin(NSPoint(x:120,y:120))
        record("programmatic reposition stays opaque", phase:"idle", alpha:1)

        // Exercise background delivery and the unchanged card controls with fake content.
        let background = NSPoint(x:panel.frame.minX+5,y:panel.frame.minY+100)
        panel.sendEvent(pointer(.leftMouseDown,screen:background))
        record("background press uses local drag",phase:"armed",alpha:1)
        move(background,dx:20,dy:10)
        record("background drag also fades",phase:"dragging",alpha:0.5)
        release(background)
        record("background mouseUp restores",phase:"idle",alpha:1)
        let json = #"{"schema_version":1,"revision":"fake","updated_at":null,"notice":"","projects":[{"key":"qa","label":"QA","updated_at":null,"items":[{"id":"fa000000-0000-4000-8000-000000000001","title":"测试标题","review_point":"只有假文字","fingerprint":"fake","action_token":"fake","actionable":true}]}]}"#
        let view = try! JSONDecoder().decode(PanelView.self,from:Data(json.utf8))
        var navigation = 0
        var actions: [String] = []
        onNavigate = { _ in navigation += 1 }
        onAction = { _, action in actions.append(action.rawValue) }
        render(view:view,expandedProjectKey:"qa",activeActionIDs:[],transientMessage:nil)
        panel.contentView?.layoutSubtreeIfNeeded()
        func descendants(_ view:NSView)->[NSView] {view.subviews.flatMap{[$0]+descendants($0)}}
        func clickControl(_ button:NSButton) {
            // NSButton tracking depends on physical pressed-button state; invoke
            // its native action here, separately from the drag-event replay above.
            button.performClick(nil)
        }
        let controls = descendants(contentStack).compactMap{$0 as? RecoveryItemButton}
        clickControl(controls.first{$0 is TextRegionButton}!)
        precondition(navigation==1 && actions.isEmpty)
        record("native text button action navigates without dragging",phase:"idle",alpha:1)
        clickControl(controls.first{$0.action == #selector(takeItem(_:))}!)
        clickControl(controls.first{$0.action == #selector(dismissItem(_:))}!)
        precondition(navigation==1 && actions==["take","dismiss"])
        record("native action buttons remain separate from drag and navigation",phase:"idle",alpha:1)
        return trace
    }
}
@main
private struct DragInputTests {
    static func main() throws {
        NSApplication.shared.setActivationPolicy(.accessory)
        let controller = PanelWindowController()
        let trace = controller.verifyInputPath()
        let result:[String:Any] = ["passed":true, "checks":trace.count, "trace":trace,
            "event_entry":"NSWindow.sendEvent with local NSEvents -> real header hit testing -> local drag",
            "manual_windowWillMove_calls":0, "system_event_injection":false,
            "physical_mouse_drag":"not_tested", "backend_called":false, "test_window_closed":true]
        print(String(data:try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]),encoding:.utf8)!)
    }
}
'''

def main():
    directory = Path(tempfile.mkdtemp(prefix='resume-desk-drag-state-'))
    source = (APP/'Sources/FloatingRecoveryApp.swift').read_text()
    assert source.count('@main\nstruct FloatingRecoveryApp') == 1
    source = source.replace('@main\nstruct FloatingRecoveryApp', 'struct FloatingRecoveryApp', 1)
    test = directory/'OpacityTests.swift'; test.write_text(source+'\n'+TEST_MAIN)
    binary = directory/'OpacityTests'
    subprocess.run(['xcrun','swiftc',str(test),'-parse-as-library','-framework','AppKit','-framework','Foundation',
        '-module-cache-path', tempfile.gettempdir()+'/resume-desk-floating-panel-module-cache','-o',str(binary)],check=True)
    result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=20)
    (directory/'stderr.log').write_text(result.stderr)
    if result.returncode: raise RuntimeError(f'Native test exited {result.returncode}: '+result.stderr[-1800:])
    parsed=json.loads(result.stdout);(directory/'result.json').write_text(json.dumps(parsed,ensure_ascii=False,indent=2))
    print(json.dumps({'result_path':str(directory/'result.json'),'result':parsed},ensure_ascii=False))

if __name__ == '__main__': main()
