from casezero_ingestion.text import extract_html_text


def test_html_text_preserves_original_character_offsets() -> None:
    source = "<html><body><p>Visible evidence</p><script>hidden()</script></body></html>"
    blocks = extract_html_text(source)
    assert [block.text for block in blocks] == ["Visible evidence"]
    block = blocks[0]
    assert source[block.start:block.end] == block.text
