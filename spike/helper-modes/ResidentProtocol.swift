import Foundation

@objc(TactoModeRPC) protocol TactoModeRPC {
    func acquire(_ build: String, reply: @escaping (NSDictionary) -> Void)
    func request(_ frame: Data, reply: @escaping (Data) -> Void)
}

enum TactoResident {
    static let service = "ai.callabo.tacto.prototype.resident"
    static let agent = "ai.callabo.tacto.prototype.agent"
}
