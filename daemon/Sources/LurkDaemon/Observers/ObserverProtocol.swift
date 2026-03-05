import Foundation

protocol Observer: AnyObject {
    var name: String { get }
    var isRunning: Bool { get }
    func start()
    func stop()
}
