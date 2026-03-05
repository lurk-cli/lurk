import AppKit

// Run as accessory app (menu bar only, no dock icon)
let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let delegate = AppDelegate()
app.delegate = delegate

app.run()
