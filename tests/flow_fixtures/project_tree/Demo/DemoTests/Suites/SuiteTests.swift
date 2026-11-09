import Testing

@Suite("SuiteA") struct SuiteATests { @Test("loads") func loads() {} }
@Suite("SuiteB") struct SuiteBTests { @Test("loads") func loads() {} }
@Suite("RightSuite") struct RightSuiteTests { @Test("loads") func loads() {} }
