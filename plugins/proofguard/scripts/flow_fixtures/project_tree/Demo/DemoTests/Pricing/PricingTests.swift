import Testing

@Suite("Pricing rules")
struct PricingTests {
    @Test("rounds half up") func roundsHalfUp() {}
    @Test func loads() {}
}

@Suite("Tagged", .tags(.fast))
nonisolated struct TaggedTests {
    @Test("tag sentence") func tagCase() {}
}

@Suite("Enum rules")
enum EnumRulesTests {
    @Test("enum sentence") static func enumCase() {}
}

@Suite("Actor rules")
actor ActorRulesTests {
    @Test("actor sentence") func actorCase() {}
}

extension PricingTests {
    @Test("extension sentence") func extensionCase() {}
}
