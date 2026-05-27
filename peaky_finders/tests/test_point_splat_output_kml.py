"""SPLAT ``output.kml`` href patch for transparent PNG."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.kml_bundle import point_splat_output_kml_at_png


def test_point_splat_output_kml_rewrites_ppm(tmp_path: Path) -> None:
    kml = tmp_path / "output.kml"
    kml.write_text(
        """<kml xmlns="http://earth.google.com/kml/2.1">
  <Folder>
    <GroundOverlay>
      <Icon>
        <href>   output.ppm  </href>
      </Icon>
    </GroundOverlay>
    <ScreenOverlay><Icon><href>output-ck.ppm</href></Icon></ScreenOverlay>
  </Folder>
</kml>""",
        encoding="utf-8",
    )
    point_splat_output_kml_at_png(kml)
    out = kml.read_text(encoding="utf-8")
    assert "<href>splat.png</href>" in out
    assert "output.ppm" not in out
    assert "output-ck.ppm" in out


def test_point_splat_output_kml_idempotent(tmp_path: Path) -> None:
    kml = tmp_path / "output.kml"
    text = "<kml><GroundOverlay><Icon><href>splat.png</href></Icon></GroundOverlay></kml>"
    kml.write_text(text, encoding="utf-8")
    point_splat_output_kml_at_png(kml)
    assert kml.read_text(encoding="utf-8") == text
