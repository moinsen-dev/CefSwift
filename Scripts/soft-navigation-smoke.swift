import Foundation

private struct DevToolsTarget: Decodable {
    let id: String
    let type: String
    let url: String
    let webSocketDebuggerUrl: URL?
}

private enum SmokeFailure: Error, CustomStringConvertible {
    case invalidArguments
    case noPageTarget
    case commandTimedOut(String)
    case malformedResponse(String)
    case softNavigationMissing

    var description: String {
        switch self {
        case .invalidArguments:
            return "usage: soft-navigation-smoke.swift [--port <1024-65535>]"
        case .noPageTarget:
            return "no HTTP(S) page target appeared before the 30 second deadline"
        case .commandTimedOut(let method):
            return "DevTools command timed out after the browser target stopped responding: \(method)"
        case .malformedResponse(let detail):
            return "malformed DevTools response: \(detail)"
        case .softNavigationMissing:
            return "the browser survived, but the soft navigation was not observed"
        }
    }
}

@main
private struct SoftNavigationSmoke {
    static func main() async {
        do {
            let port = try parsePort(arguments: Array(CommandLine.arguments.dropFirst()))
            try await run(port: port)
        } catch {
            FileHandle.standardError.write(Data("[soft-navigation-smoke] FAIL: \(error)\n".utf8))
            exit(1)
        }
    }

    private static func parsePort(arguments: [String]) throws -> Int {
        guard !arguments.isEmpty else { return 9223 }
        guard arguments.count == 2,
              arguments[0] == "--port",
              let port = Int(arguments[1]),
              (1_024...65_535).contains(port)
        else {
            throw SmokeFailure.invalidArguments
        }
        return port
    }

    private static func run(port: Int) async throws {
        let target = try await waitForPageTarget(port: port)
        guard let webSocketURL = target.webSocketDebuggerUrl else {
            throw SmokeFailure.malformedResponse("page target has no WebSocket URL")
        }

        let socket = URLSession.shared.webSocketTask(with: webSocketURL)
        socket.resume()
        defer { socket.cancel(with: .normalClosure, reason: nil) }

        let installHandler = #"onclick=()=>{history.pushState(0,"","?soft-nav="+Date.now());document.body.innerHTML="<h1 style='font-size:15vw'>SOFT NAVIGATION</h1>"}"#
        _ = try await sendCommand(
            id: 1,
            method: "Runtime.evaluate",
            params: ["expression": installHandler],
            over: socket
        )
        _ = try await sendCommand(
            id: 2,
            method: "Input.dispatchMouseEvent",
            params: [
                "type": "mousePressed",
                "x": 80,
                "y": 80,
                "button": "left",
                "clickCount": 1,
            ],
            over: socket
        )
        _ = try await sendCommand(
            id: 3,
            method: "Input.dispatchMouseEvent",
            params: [
                "type": "mouseReleased",
                "x": 80,
                "y": 80,
                "button": "left",
                "clickCount": 1,
            ],
            over: socket
        )

        try await Task.sleep(for: .seconds(1))
        let response = try await sendCommand(
            id: 4,
            method: "Runtime.evaluate",
            params: ["expression": "location.href", "returnByValue": true],
            over: socket
        )
        guard let result = response["result"] as? [String: Any],
              let nestedResult = result["result"] as? [String: Any],
              let currentURL = nestedResult["value"] as? String
        else {
            throw SmokeFailure.malformedResponse("location.href result is missing")
        }
        guard currentURL.contains("?soft-nav=") else {
            throw SmokeFailure.softNavigationMissing
        }

        print("[soft-navigation-smoke] PASS: Alloy browser survived a real soft navigation")
    }

    private static func waitForPageTarget(port: Int) async throws -> DevToolsTarget {
        let endpoint = URL(string: "http://127.0.0.1:\(port)/json/list")!
        let deadline = Date().addingTimeInterval(30)
        var previousTargetID: String?
        var stableObservations = 0

        while Date() < deadline {
            do {
                let (data, response) = try await URLSession.shared.data(from: endpoint)
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    let targets = try JSONDecoder().decode([DevToolsTarget].self, from: data)
                    if let page = targets.first(where: {
                        $0.type == "page"
                            && ($0.url.hasPrefix("http://") || $0.url.hasPrefix("https://"))
                            && $0.webSocketDebuggerUrl != nil
                    }) {
                        if page.id == previousTargetID {
                            stableObservations += 1
                        } else {
                            previousTargetID = page.id
                            stableObservations = 1
                        }
                        if stableObservations >= 5 {
                            return page
                        }
                    } else {
                        previousTargetID = nil
                        stableObservations = 0
                    }
                }
            } catch {
                // The app or its first page may still be starting.
            }
            try await Task.sleep(for: .milliseconds(200))
        }

        throw SmokeFailure.noPageTarget
    }

    private static func sendCommand(
        id: Int,
        method: String,
        params: [String: Any],
        over socket: URLSessionWebSocketTask
    ) async throws -> [String: Any] {
        let data = try JSONSerialization.data(withJSONObject: [
            "id": id,
            "method": method,
            "params": params,
        ])
        guard let message = String(data: data, encoding: .utf8) else {
            throw SmokeFailure.malformedResponse("command is not UTF-8")
        }
        try await socket.send(.string(message))

        while true {
            let reply = try await receive(from: socket, method: method)
            let replyData: Data
            switch reply {
            case .string(let text):
                replyData = Data(text.utf8)
            case .data(let data):
                replyData = data
            @unknown default:
                throw SmokeFailure.malformedResponse("unknown WebSocket message")
            }
            guard let object = try JSONSerialization.jsonObject(with: replyData) as? [String: Any] else {
                throw SmokeFailure.malformedResponse("response is not an object")
            }
            guard object["id"] as? Int == id else {
                continue
            }
            if let error = object["error"] {
                throw SmokeFailure.malformedResponse("DevTools returned \(error)")
            }
            return object
        }
    }

    private static func receive(
        from socket: URLSessionWebSocketTask,
        method: String
    ) async throws -> URLSessionWebSocketTask.Message {
        try await withThrowingTaskGroup(of: URLSessionWebSocketTask.Message.self) { group in
            group.addTask {
                try await socket.receive()
            }
            group.addTask {
                try await Task.sleep(for: .seconds(5))
                throw SmokeFailure.commandTimedOut(method)
            }
            guard let first = try await group.next() else {
                throw SmokeFailure.malformedResponse("no response for \(method)")
            }
            group.cancelAll()
            return first
        }
    }
}
