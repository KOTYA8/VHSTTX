import unittest

import click

from teletext.gui.launcher import (
    ParamDescriptor,
    SQUASHTOOL_COMMAND_PATH,
    TTEDITOR_COMMAND_PATH,
    TTVIEWER_COMMAND_PATH,
    _display_version_text,
    _normalise_version_text,
    build_command_tree,
    compare_versions,
    command_visible_for_platform,
    describe_command,
    filter_descriptors_for_platform,
    format_command_label,
    is_basic_descriptor,
    launcher_process_command,
    iter_leaf_nodes,
    is_windows_runtime,
    list_mode_leaf_nodes,
    load_teletext_root_command,
    LIST_MODE_PRIMARY,
    order_param_descriptors,
    parse_latest_release_payload,
    preview_command_text,
    split_text_values,
)


@click.group()
def dummy():
    pass


@dummy.command()
@click.argument("input_path")
@click.option("--mode", type=click.Choice(["slice", "deconvolve"]), default="deconvolve", help="Decode mode.")
@click.option("--show-quality/--no-show-quality", default=False, help="Toggle diagnostics.")
@click.option("--output", "-o", type=(click.Choice(["auto", "ansi"]), click.File("w")), multiple=True, default=[("auto", "-")])
@click.option("--page", multiple=True)
def run(input_path, mode, show_quality, output, page):
    pass


@dummy.group()
def nested():
    pass


@nested.command("child")
@click.argument("target", required=False)
def nested_child(target):
    pass


class TestLauncherHelpers(unittest.TestCase):
    def test_build_command_tree_collects_nested_leaf_paths(self):
        tree = build_command_tree(dummy)
        leaf_paths = sorted(node.path for node in iter_leaf_nodes(tree))
        self.assertEqual(leaf_paths, [("nested", "child"), ("run",)])

    def test_describe_command_detects_choice_and_flag_pairs(self):
        params = {descriptor.name: descriptor for descriptor in describe_command(run)}
        self.assertEqual(params["mode"].value_kind, "choice")
        self.assertEqual(params["mode"].choices, ("slice", "deconvolve"))
        self.assertTrue(params["show_quality"].is_flag)
        self.assertEqual(params["show_quality"].secondary_option_names, ("--no-show-quality",))
        self.assertEqual(params["input_path"].param_type, "argument")
        self.assertEqual(params["output"].value_kind, "redirect_output")
        self.assertEqual(params["output"].choices, ("auto", "ansi"))
        self.assertEqual(params["output"].default_text, "")
        self.assertTrue(is_basic_descriptor(params["output"]))
        self.assertFalse(is_basic_descriptor(params["show_quality"]))

    def test_split_text_values_supports_spaces_commas_and_quotes(self):
        self.assertEqual(split_text_values("100,101,102"), ["100", "101", "102"])
        self.assertEqual(split_text_values("100 101 102"), ["100", "101", "102"])
        self.assertEqual(split_text_values('"page 100" 101'), ["page 100", "101"])

    def test_preview_command_text_contains_command_and_args(self):
        preview = preview_command_text(["teletext", "deconvolve", "--mode", "slice", "input.vbi"])
        self.assertIn("teletext", preview)
        self.assertIn("deconvolve", preview)
        self.assertIn("input.vbi", preview)

    def test_version_helpers_normalise_compare_and_display(self):
        self.assertEqual(_normalise_version_text("V2.6"), (2, 6))
        self.assertEqual(_display_version_text("2.6"), "V2.6")
        self.assertEqual(compare_versions("2.6", "v2.7"), 1)
        self.assertEqual(compare_versions("2.6", "2.6"), 0)
        self.assertEqual(format_command_label("vbirepair"), "VBI Repair")
        self.assertEqual(format_command_label("vbiview"), "VBI View")
        self.assertEqual(format_command_label("t42tool"), "T42 Tool")
        self.assertEqual(format_command_label("squashtool"), "Squash Tool")
        self.assertEqual(format_command_label("spellcheck-analyze"), "Spellcheck Analyze")

    def test_primary_mode_contains_expected_main_commands(self):
        tree = build_command_tree(load_teletext_root_command())
        paths = [node.path for node in list_mode_leaf_nodes(tree, LIST_MODE_PRIMARY)]
        self.assertEqual(
            paths[:9],
            [
                ("vbiview",),
                ("vbitool",),
                ("vbirepair",),
                ("deconvolve",),
                ("t42tool",),
                SQUASHTOOL_COMMAND_PATH,
                ("squash",),
                TTVIEWER_COMMAND_PATH,
                TTEDITOR_COMMAND_PATH,
            ],
        )
        self.assertIn(("vbirepair",), paths)
        self.assertIn(("vbitool",), paths)
        self.assertIn(("t42tool",), paths)
        self.assertIn(TTVIEWER_COMMAND_PATH, paths)
        self.assertIn(TTEDITOR_COMMAND_PATH, paths)

    def test_windows_profile_hides_record_and_linux_only_options(self):
        self.assertTrue(is_windows_runtime("nt"))
        self.assertFalse(command_visible_for_platform(type("Node", (), {"path": ("record",)})(), "nt"))
        self.assertTrue(command_visible_for_platform(type("Node", (), {"path": ("deconvolve",)})(), "nt"))
        descriptors = [
            ParamDescriptor("device", "option", "--device", "", False, False, 1),
            ParamDescriptor("fix_capture_card", "option", "--fix-capture-card", "", False, False, 1),
            ParamDescriptor("page", "option", "--page", "", False, False, 1),
        ]
        filtered = filter_descriptors_for_platform(descriptors, ("deconvolve",), "nt")
        self.assertEqual([descriptor.name for descriptor in filtered], ["page"])

    def test_parse_latest_release_payload_extracts_release_url(self):
        release = parse_latest_release_payload({
            "tag_name": "v2.7",
            "html_url": "https://github.com/KOTYA8/VHSTTX/releases/tag/v2.7",
            "name": "VHSTTX 2.7",
        })
        self.assertEqual(release["display_version"], "V2.7")
        self.assertEqual(release["version_tuple"], (2, 7))
        self.assertIn("/releases/tag/v2.7", release["html_url"])

    def test_order_param_descriptors_prefers_saved_order(self):
        descriptors = describe_command(run)
        ordered = order_param_descriptors(descriptors, ["page", "input_path", "mode"])
        self.assertEqual(
            [descriptor.name for descriptor in ordered[:3]],
            ["page", "input_path", "mode"],
        )

    def test_launcher_process_command_non_frozen_teletext(self):
        program, args = launcher_process_command(["teletext", "deconvolve", "test.vbi"])
        self.assertTrue(program)
        self.assertTrue(args)

    def test_launcher_process_command_non_frozen_squashtool(self):
        program, args = launcher_process_command(["squashtool"])
        self.assertEqual(program, "squashtool")
        self.assertEqual(args, [])


if __name__ == "__main__":
    unittest.main()
