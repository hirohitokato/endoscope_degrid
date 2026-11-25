import AppKit
import CoreGraphics
import Foundation

let app = Process()
app.executableURL = URL(fileURLWithPath: CommandLine.arguments[1])
app.currentDirectoryURL = URL(fileURLWithPath: NSTemporaryDirectory())
try app.run()
defer { if app.isRunning { app.terminate(); app.waitUntilExit() } }
let deadline = Date().addingTimeInterval(35)
var found = false
while Date() < deadline && app.isRunning {
    let entries = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]) ?? []
    found = entries.contains { window in
        guard window[kCGWindowOwnerPID as String] as? Int32 == app.processIdentifier,
              window[kCGWindowLayer as String] as? Int == 0,
              let bounds = window[kCGWindowBounds as String] as? [String: Double] else { return false }
        return bounds["Width", default: 0] > 200 && bounds["Height", default: 0] > 80
    }
    if found { break }
    Thread.sleep(forTimeInterval: 0.25)
}
if !found { throw NSError(domain: "Desktop window was not displayed", code: 1) }
print("Packaged Mac GUI displayed a window")
