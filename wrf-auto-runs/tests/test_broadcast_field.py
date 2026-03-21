import pytest

from set_params import broadcast_field


class TestBroadcastField:
    def test_scalar_broadcasts_to_n_domains(self):
        assert broadcast_field(5, 3, [1, 2, 3], 3) == [5, 5, 5]

    def test_full_array_passes_through(self):
        assert broadcast_field([1, 2, 3], 3, [1, 2, 3], 3) == [1, 2, 3]

    def test_full_array_sliced_to_subset(self):
        assert broadcast_field([10, 20, 30], 2, [1, 3], 3) == [10, 30]

    def test_single_domain_subset(self):
        assert broadcast_field([10, 20, 30], 1, [2], 3) == [20]

    def test_boolean_broadcast(self):
        assert broadcast_field(True, 3, [1, 2, 3], 3) == [True, True, True]

    def test_float_array_sliced(self):
        assert broadcast_field([0.1, 0.2, 0.3], 2, [2, 3], 3) == [0.2, 0.3]

    def test_wrong_size_raises_valueerror(self):
        with pytest.raises(ValueError, match='expected'):
            broadcast_field([1, 2], 3, [1, 2, 3], 4)

    def test_already_correct_size_passes_through(self):
        assert broadcast_field([1, 2], 2, [1, 2], 6) == [1, 2]
