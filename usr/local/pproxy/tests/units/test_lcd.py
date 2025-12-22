
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, mock_open
import logging.config
import logging.config
# Mock logging.config BEFORE it's called at top level of lcd.py
logging.config.fileConfig = MagicMock()

# autopep8: off
# Add parent directory to path to import lcd
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)

import lcd
from lcd import LCD
import RPi.GPIO as GPIO
# autopep8: on


class TestLCD(unittest.TestCase):

    @patch('lcd.configparser.ConfigParser')
    @patch('lcd.logging.config.fileConfig')
    def setUp(self, mock_fileConfig, mock_config_class):
        self.mock_config = mock_config_class.return_value
        self.mock_config.getint.side_effect = self._mock_getint
        self.mock_config.read.return_value = None

        # Default mock values
        self.lcd_present = 1
        self.lcd_version = 2

        # Patching inside LCD.__init__
        with patch('lcd.board.SPI') as mock_spi, \
                patch('lcd.digitalio.DigitalInOut') as mock_dio:
            self.lcd_instance = LCD()

    def _mock_getint(self, section, option):
        if section == 'hw' and option == 'lcd':
            return self.lcd_present
        if section == 'hw' and option == 'lcd-version':
            return self.lcd_version
        return 0

    def test_init_version_2(self):
        self.assertEqual(self.lcd_instance.version, 2)
        self.assertEqual(self.lcd_instance.width, 240)
        self.assertEqual(self.lcd_instance.height, 240)

    @patch('lcd.configparser.ConfigParser')
    def test_init_version_1(self, mock_config_class):
        self.lcd_version = 1
        mock_config = mock_config_class.return_value
        mock_config.getint.side_effect = self._mock_getint

        with patch('lcd.Adafruit_SSD1306.SSD1306_128_64') as mock_ssd:
            l = LCD()
            self.assertEqual(l.version, 1)

    @patch('lcd.configparser.ConfigParser')
    def test_init_lcd_not_present(self, mock_config_class):
        self.lcd_present = 0
        self.lcd_version = 2
        mock_config = mock_config_class.return_value
        mock_config.getint.side_effect = self._mock_getint

        l = LCD()
        self.assertEqual(l.lcd_present, 0)
        self.assertEqual(l.width, 240)

    def test_set_lcd_present(self):
        self.lcd_instance.set_lcd_present(False)
        self.assertEqual(self.lcd_instance.lcd_present, 0)
        self.lcd_instance.set_lcd_present(True)
        self.assertEqual(self.lcd_instance.lcd_present, 1)

    def test_clear(self):
        with patch.object(self.lcd_instance, 'display') as mock_display:
            self.lcd_instance.clear()
            mock_display.assert_called_with((), 0)

    def test_set_backlight(self):
        # Case lcd_present = 1
        with patch('lcd.GPIO.setup') as mock_setup, \
                patch('lcd.GPIO.output') as mock_output:
            self.lcd_instance.set_backlight(True)
            self.assertTrue(self.lcd_instance.backlight_state_on)

            self.lcd_instance.set_backlight(False)
            self.assertFalse(self.lcd_instance.backlight_state_on)

    def test_get_backlight_is_on(self):
        with patch('lcd.GPIO.input') as mock_input, \
                patch('lcd.GPIO.setup'):
            mock_input.return_value = GPIO.HIGH
            self.assertTrue(self.lcd_instance.get_backlight_is_on())

            mock_input.return_value = GPIO.LOW
            self.assertFalse(self.lcd_instance.get_backlight_is_on())

    @patch('lcd.Image.new')
    @patch('lcd.ImageDraw.Draw')
    @patch('lcd.ImageFont.truetype')
    def test_display_text(self, mock_font, mock_draw, mock_image_new):
        strs = [(1, "Hello", 0, "white")]
        self.lcd_instance.lcd_present = 1
        self.lcd_instance.version = 2

        self.lcd_instance.display(strs, 20)

        # Verify drawing was called
        mock_draw.return_value.text.assert_called()

    @patch('lcd.Image.new')
    @patch('lcd.ImageDraw.Draw')
    @patch('lcd.ImageFont.truetype')
    def test_display_icon(self, mock_font, mock_draw, mock_image_new):
        strs = [(1, "icon", 1, "red")]
        self.lcd_instance.lcd_present = 1
        self.lcd_instance.version = 2

        self.lcd_instance.display(strs, 20)
        mock_draw.return_value.text.assert_called()

    @patch('lcd.qrcode.QRCode')
    @patch('lcd.Image.new')
    @patch('lcd.ImageDraw.Draw')
    def test_display_qr(self, mock_draw, mock_image_new, mock_qr):
        strs = [(1, "data", 2, "white")]
        self.lcd_instance.lcd_present = 1
        self.lcd_instance.version = 2

        self.lcd_instance.display(strs, 20)
        mock_qr.assert_called()

    def test_set_logo_text(self):
        self.lcd_instance.set_logo_text("WEPN", x=10, y=10)
        self.assertEqual(self.lcd_instance.logo_text, "WEPN")
        self.assertEqual(self.lcd_instance.logo_text_x, 10)

    @patch('lcd.Image.new')
    def test_show_image(self, mock_image_new):
        mock_img = MagicMock()
        self.lcd_instance.lcd_present = 0
        self.lcd_instance.show_image(mock_img)
        mock_img.save.assert_called()

    @patch('lcd.Image.open')
    @patch('lcd.ImageDraw.Draw')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageFilter.GaussianBlur')
    def test_show_logo_v2(self, mock_blur, mock_font, mock_draw, mock_open_img):
        self.lcd_instance.lcd_present = 1
        self.lcd_instance.version = 2
        self.lcd_instance.logo_text = "test"
        self.lcd_instance.logo_text_size = 15

        # Mock Image.open to return a mock image
        mock_img = MagicMock()
        mock_open_img.return_value = mock_img
        mock_img.convert.return_value = mock_img
        mock_img.filter.return_value = mock_img

        self.lcd_instance.show_logo()
        mock_open_img.assert_called()

    @patch('lcd.Image.open')
    def test_play_animation(self, mock_open_img):
        self.lcd_instance.lcd_present = 1
        self.lcd_instance.version = 2

        mock_img = MagicMock()
        mock_open_img.return_value.__enter__.return_value = mock_img

        with patch('lcd.ImageSequence.Iterator') as mock_iter:
            mock_iter.return_value = [MagicMock()]
            self.lcd_instance.play_animation("test.gif", loop_count=1)
            mock_iter.assert_called()

    def test_get_status_icons(self):
        # status 0, not connected
        ret, any_err = self.lcd_instance.get_status_icons(0, False, False)
        self.assertTrue(any_err)

        # status 2 (on), connected
        ret, any_err = self.lcd_instance.get_status_icons(2, True, True)
        self.assertFalse(any_err)

    def test_get_status_icons_v2(self):
        from constants import HEALTHY_DIAG_CODE
        ret, any_err, errs = self.lcd_instance.get_status_icons_v2(2, HEALTHY_DIAG_CODE)
        self.assertFalse(any_err)
        self.assertEqual(len(errs), 7)

    @patch('lcd.Image.new')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageDraw.Draw')
    def test_show_menu(self, mock_draw, mock_font, mock_image_new):
        self.lcd_instance.width = 240
        self.lcd_instance.height = 240
        self.lcd_instance.menu_row_y_size = 40
        self.lcd_instance.menu_row_skip = 10

        # Use create=True because half_round_rectangle is missing in LCD class
        with patch.object(self.lcd_instance, 'half_round_rectangle', return_value=MagicMock(), create=True):
            self.lcd_instance.show_menu("Title", ["Item1", "Item2"])
            mock_draw.assert_called()

    @patch('lcd.Image.new')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageDraw.Draw')
    def test_show_prompt(self, mock_draw, mock_font, mock_image_new):
        self.lcd_instance.menu_row_y_size = 40
        self.lcd_instance.menu_row_skip = 10
        # Mock fnt.getsize to return a tuple
        mock_font.return_value.getsize.return_value = (50, 20)

        with patch.object(self.lcd_instance, 'half_round_rectangle', return_value=MagicMock(), create=True):
            self.lcd_instance.show_prompt("Title", [{"text": "Yes", "color": "green"}])
            mock_draw.assert_called()

    @patch('lcd.Image.new')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageDraw.Draw')
    def test_progress_wheel(self, mock_draw, mock_font, mock_image_new):
        # Mock fnt.getsize to return a tuple
        mock_font.return_value.getsize.return_value = (50, 20)
        self.lcd_instance.progress_wheel("Loading", 90, "blue")
        mock_draw.return_value.arc.assert_called()

    @patch('lcd.Image.new')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageDraw.Draw')
    def test_show_summary(self, mock_draw, mock_font, mock_image_new):
        self.lcd_instance.lcd_present = 1
        self.lcd_instance.version = 2
        strs = [("Label", "Icon", "white", "red")]
        self.lcd_instance.show_summary(strs, 20)
        mock_draw.return_value.text.assert_called()

    @patch('lcd.Image.new')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageDraw.Draw')
    def test_show_prompt_long_title(self, mock_draw, mock_font, mock_image_new):
        long_title = "This title is definitely more than thirty characters long and should raise an exception"
        with self.assertRaisesRegex(Exception, "The title text is too long"):
            self.lcd_instance.show_prompt(long_title)

    @patch('lcd.Image.new')
    @patch('lcd.ImageFont.truetype')
    @patch('lcd.ImageDraw.Draw')
    def test_progress_wheel_long_title(self, mock_draw, mock_font, mock_image_new):
        mock_font.return_value.getsize.return_value = (50, 20)
        long_title = "This title is very very very long indeed"
        with self.assertRaisesRegex(Exception, "The title text is too long"):
            self.lcd_instance.progress_wheel(long_title, 90, "red")

    @patch('builtins.open', new_callable=mock_open)
    def test_display_lcd_not_present_file_output(self, mock_file):
        self.lcd_instance.lcd_present = 0
        strs = [(1, "Test", 0, "white")]
        self.lcd_instance.display(strs, 20)
        mock_file.assert_called_with(lcd.TEXT_OUT, 'w')

    @patch('lcd.Image.new')
    @patch('builtins.open', new_callable=mock_open)
    def test_display_lcd_present_0_saves_image(self, mock_file, mock_image_new):
        self.lcd_instance.lcd_present = 0
        self.lcd_instance.version = 1  # SSD1306 path

        mock_disp = MagicMock()
        mock_disp.width = 128
        mock_disp.height = 64

        with patch('lcd.Adafruit_SSD1306.SSD1306_128_64', return_value=mock_disp), \
                patch('lcd.ImageDraw.Draw'):
            self.lcd_instance.display([(1, "Test", 0, "white")], 20)
            # image.save(IMG_OUT) should be called in line 223
            mock_image_new.return_value.save.assert_called_with(lcd.IMG_OUT)

    def test_long_text(self):
        with patch.object(self.lcd_instance, 'display') as mock_display:
            self.lcd_instance.long_text("Some very long text that should wrap hopefully")
            mock_display.assert_called()

    def test_long_text_simple(self):
        with patch.object(self.lcd_instance, 'display') as mock_display:
            self.lcd_instance.long_text("Hello\nWorld")
            mock_display.assert_called()


if __name__ == '__main__':
    unittest.main()
