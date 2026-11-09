// Demo business rules.
struct Rules {
    static func total(_ items: [Int]) -> Int { items.reduce(0, +) }
}
