import AppKit
import Foundation

let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(calibratedRed: 0.13, green: 0.46, blue: 0.41, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 210, yRadius: 210).fill()
NSColor.white.setStroke()
let arrow = NSBezierPath()
arrow.lineWidth = 67
arrow.lineCapStyle = .round
arrow.lineJoinStyle = .round
arrow.move(to: NSPoint(x: 302, y: 302))
arrow.line(to: NSPoint(x: 722, y: 722))
arrow.move(to: NSPoint(x: 351, y: 722))
arrow.line(to: NSPoint(x: 722, y: 722))
arrow.line(to: NSPoint(x: 722, y: 351))
arrow.stroke()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
