"""Unit tests for the user-agent / network feature extractors in ``Bid``.

These are pure functions used during feature engineering. Importing ``Bid``
pulls in numpy/pandas/joblib (declared in requirements.txt) but does NOT load
the model artifacts — that only happens in ``Bid.__init__``.
"""
import Bid


class TestDeviceFamily:
    def test_iphone_is_mobile(self):
        assert Bid.xtract_device_family("iPhone; CPU iPhone OS") == "Mobile"

    def test_android_is_mobile(self):
        assert Bid.xtract_device_family("Linux; Android 10; Mobile") == "Mobile"

    def test_ipad_is_tablet(self):
        assert Bid.xtract_device_family("iPad; CPU OS 13") == "Tablet"

    def test_windows_is_desktop(self):
        assert Bid.xtract_device_family("Windows NT 10.0; Win64") == "Desktop"

    def test_non_string_is_unknown(self):
        assert Bid.xtract_device_family(None) == "Unknown"
        assert Bid.xtract_device_family(12345) == "Unknown"

    def test_unrecognised_is_other(self):
        assert Bid.xtract_device_family("some-random-bot/1.0") == "Other"


class TestOsFamily:
    def test_windows(self):
        assert Bid.xtract_os_family("Windows NT 10.0") == "Windows"

    def test_macos(self):
        assert Bid.xtract_os_family("Macintosh; Intel Mac OS X") == "MacOS"

    def test_android(self):
        assert Bid.xtract_os_family("Linux; Android 11") == "Android"

    def test_ios_via_iphone(self):
        assert Bid.xtract_os_family("iPhone OS 14_0") == "iOS"

    def test_non_string_is_unknown(self):
        assert Bid.xtract_os_family(None) == "Unknown"


class TestBrowserFamily:
    def test_chrome(self):
        assert Bid.xtract_browser_family("Mozilla Chrome/120 Safari") == "Chrome"

    def test_safari_excludes_chrome(self):
        # Chrome UA strings also contain "Safari"; must not be misclassified.
        assert Bid.xtract_browser_family("Macintosh Safari/605") == "Safari"
        assert Bid.xtract_browser_family("Windows Chrome/120 Safari/537") == "Chrome"

    def test_firefox(self):
        assert Bid.xtract_browser_family("Gecko Firefox/121") == "Firefox"

    def test_internet_explorer_via_trident(self):
        assert Bid.xtract_browser_family("Mozilla; Trident/7.0") == "IE"

    def test_non_string_is_unknown(self):
        assert Bid.xtract_browser_family(None) == "Unknown"


class TestNetworkClass:
    def test_class_a(self):
        assert Bid.xtract_network_class("10.0.0.1") == 1

    def test_class_b(self):
        assert Bid.xtract_network_class("130.0.0.1") == 2

    def test_class_c(self):
        assert Bid.xtract_network_class("200.0.0.1") == 3

    def test_non_string_returns_zero(self):
        assert Bid.xtract_network_class(None) == 0
