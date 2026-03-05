import Foundation
#if canImport(Darwin)
import Darwin
#endif

/// Self-monitoring observer that tracks CPU/memory usage and throttles polling when needed.
/// Sets process to background priority. Samples every 30s with a 10-sample ring buffer.
final class PerformanceMonitor: Observer {
    let name = "Performance"
    private(set) var isRunning = false
    private weak var manager: ObserverManager?
    private var timer: DispatchSourceTimer?

    private let sampleInterval: TimeInterval = 30.0
    private let cpuThreshold: Double = 2.0     // percent
    private let memoryWarnMB: Double = 50.0

    // Ring buffer of CPU samples
    private var cpuSamples: [Double] = []
    private let maxSamples = 10
    private var isThrottled = false

    init(manager: ObserverManager) {
        self.manager = manager
    }

    func start() {
        // Set process to background priority
        setBackgroundPriority()

        let timer = DispatchSource.makeTimerSource(queue: .global(qos: .background))
        timer.schedule(deadline: .now() + sampleInterval, repeating: sampleInterval)
        timer.setEventHandler { [weak self] in
            self?.sample()
        }
        timer.resume()
        self.timer = timer
        isRunning = true
    }

    func stop() {
        timer?.cancel()
        timer = nil
        isRunning = false
    }

    // MARK: - Sampling

    private func sample() {
        let cpu = sampleCPU()
        let memMB = sampleMemoryMB()

        // Ring buffer
        cpuSamples.append(cpu)
        if cpuSamples.count > maxSamples {
            cpuSamples.removeFirst()
        }

        // Check CPU threshold
        if cpu > cpuThreshold {
            if !isThrottled {
                isThrottled = true
                manager?.adaptiveTimer.applyThrottle(multiplier: 2.0)
                log("CPU at \(String(format: "%.1f", cpu))% — throttling polling intervals 2x")
            }
        } else if isThrottled {
            isThrottled = false
            manager?.adaptiveTimer.resetThrottle()
            log("CPU back to \(String(format: "%.1f", cpu))% — resuming normal intervals")
        }

        // Memory warning
        if memMB > memoryWarnMB {
            log("WARNING: Resident memory \(String(format: "%.1f", memMB))MB exceeds \(Int(memoryWarnMB))MB threshold")
        }
    }

    // MARK: - CPU Sampling via Mach thread info

    private func sampleCPU() -> Double {
        var threadList: thread_act_array_t?
        var threadCount: mach_msg_type_number_t = 0

        let result = task_threads(mach_task_self_, &threadList, &threadCount)
        guard result == KERN_SUCCESS, let threads = threadList else {
            return 0.0
        }
        defer {
            vm_deallocate(
                mach_task_self_,
                vm_address_t(bitPattern: threads),
                vm_size_t(Int(threadCount) * MemoryLayout<thread_act_t>.stride)
            )
        }

        var totalCPU: Double = 0.0

        for i in 0..<Int(threadCount) {
            var info = thread_basic_info_data_t()
            var infoCount = mach_msg_type_number_t(
                MemoryLayout<thread_basic_info_data_t>.size / MemoryLayout<natural_t>.size
            )

            let kr = withUnsafeMutablePointer(to: &info) { infoPtr in
                infoPtr.withMemoryRebound(to: integer_t.self, capacity: Int(infoCount)) { rawPtr in
                    thread_info(threads[i], thread_flavor_t(THREAD_BASIC_INFO), rawPtr, &infoCount)
                }
            }

            if kr == KERN_SUCCESS {
                let usage = Double(info.cpu_usage) / Double(TH_USAGE_SCALE) * 100.0
                totalCPU += usage
            }
        }

        return totalCPU
    }

    // MARK: - Memory Sampling

    private func sampleMemoryMB() -> Double {
        var info = mach_task_basic_info_data_t()
        var count = mach_msg_type_number_t(
            MemoryLayout<mach_task_basic_info_data_t>.size / MemoryLayout<natural_t>.size
        )

        let kr = withUnsafeMutablePointer(to: &info) { infoPtr in
            infoPtr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rawPtr in
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), rawPtr, &count)
            }
        }

        guard kr == KERN_SUCCESS else { return 0.0 }
        return Double(info.resident_size) / (1024.0 * 1024.0)
    }

    // MARK: - Background Priority

    private func setBackgroundPriority() {
        // Lower process priority
        setpriority(PRIO_PROCESS, 0, 20)

        // Process-level hint is set via priority above;
        // all our timers use .background QoS
    }

    private func log(_ message: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        print("[lurk \(formatter.string(from: Date()))] [Perf] \(message)")
    }
}
