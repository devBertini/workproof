import XCTest
import Testing

final class CoreRulesTests: XCTestCase {
    func testRule() {}
    func testBroken() {}
}

@Suite("Engine")
struct EngineTests {
    @Test func engineRuns() {}
    @Test func engineFails() {}
}
