"""Direct JML must commit and yield at its destination in either tier."""
import unittest
from test_call_instruction_timing import CallInstructionTiming


class TailInstructionTiming(CallInstructionTiming):
    def test_calls_and_instruction_boundaries(self):
        self.check_calls(False, tail=True)

    def test_calls_with_global_bus_timing(self):
        self.check_calls(True, tail=True)


# Avoid collecting the imported base class a second time.
del CallInstructionTiming

if __name__ == '__main__':
    unittest.main()
