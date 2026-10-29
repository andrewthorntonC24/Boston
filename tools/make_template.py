"""One-time conversion: dashboard HTML -> template with {{placeholders}}.

Usage: python tools/make_template.py boston_events_dashboard_v3.html templates/dashboard.template.html

CSS, page structure, the rubric text, and every static paragraph are left untouched.
Only data-driven regions are replaced with placeholders. Re-running on an already
templated file will fail loudly, which is intentional.
"""
import re
import sys


def cut_between(s, start, end, token, include_start=False, include_end=False):
    a = s.index(start)
    b = s.index(end, a + len(start))
    a2 = a if include_start else a + len(start)
    b2 = b + len(end) if include_end else b
    return s[:a2] + token + s[b2:]


def replace_js_const(s, name, token, opener, closer):
    """Replace `const NAME = <opener> ... <closer>;` with `const NAME = {{token}};`.
    Uses a string-aware bracket matcher so brackets inside strings don't confuse it."""
    m = re.search(r"const %s = " % re.escape(name), s)
    if not m:
        raise SystemExit("missing const " + name)
    i = m.end()
    assert s[i] == opener, (name, s[i])
    depth, j, in_str, esc, quote = 0, i, False, False, ""
    while j < len(s):
        c = s[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
        else:
            if c in "\"'`":
                in_str, quote = True, c
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    break
        j += 1
    return s[: m.start()] + "const %s = {{%s}}" % (name, token) + s[j + 1:]


def main(src, dst):
    s = open(src, encoding="utf-8").read()
    if "{{" in s.split("<script>")[0]:
        raise SystemExit("input already looks templated")

    # header snapshot line
    s = re.sub(r'<p class="meta">Data Snapshot:.*?</p>', "{{snapshot_line}}", s, count=1, flags=re.S)

    # glance
    s = re.sub(r'(<h2 id="glance-title">).*?(</h2>)', r"\1{{glance_title}}\2", s, count=1, flags=re.S)
    s = cut_between(s, '<section class="section glance" aria-labelledby="glance-title">',
                    "</section>", "", include_start=True, include_end=True) if False else s
    a = s.index('<h2 id="glance-title">')
    ul_a = s.index("<ul>", a) + len("<ul>")
    ul_b = s.index("</ul>", ul_a)
    s = s[:ul_a] + "\n{{glance_items}}\n    " + s[ul_b:]

    # events table body and note
    s = cut_between(s, '<tbody id="event-body">', "</tbody>", "\n{{event_rows}}\n      ")
    s = re.sub(r'<p class="meta" style="margin-bottom:8px">Top 10 events.*?</p>', "{{events_note}}", s, count=1, flags=re.S)

    # weather
    s = re.sub(r'(<h2 id="weather-title">).*?(</h2>)', r"\1{{weather_title}}\2", s, count=1, flags=re.S)
    w = s.index('aria-labelledby="weather-title"')
    m = re.search(r'<p class="meta">.*?</p>', s[w:], flags=re.S)
    s = s[: w + m.start()] + "{{weather_meta}}" + s[w + m.end():]
    w = s.index('aria-labelledby="weather-title"')
    g = s.index('<div class="forecast-grid">', w) + len('<div class="forecast-grid">')
    aq = s.index('<div class="aq">', g)
    s = s[:g] + "\n{{weather_cards}}\n    </div>\n    " + "{{aq_block}}" + s[s.index("</section>", aq):].replace("</section>", "\n  </section>", 1)

    # civil unrest
    u = s.index('aria-labelledby="unrest-title"')
    m = re.search(r'<p class="meta">.*?</p>', s[u:], flags=re.S)
    s = s[: u + m.start()] + "{{unrest_meta}}" + s[u + m.end():]
    u = s.index('aria-labelledby="unrest-title"')
    tb = s.index("<tbody>", u) + len("<tbody>")
    te = s.index("</tbody>", tb)
    s = s[:tb] + "\n{{unrest_rows}}\n      " + s[te:]
    te = s.index("</tbody>", s.index("{{unrest_rows}}"))
    sub = s.index('<h3 class="sub">Civil Unrest Map</h3>', te)
    s = s[: s.index("</table>", te) + len("</table>")] + "\n    {{unrest_notes}}\n\n    " + s[sub:]
    s = re.sub(r'(<p class="stamp">Pin is approximate and marks the Burlington ICE office).*?(</p>)',
               r"{{unrest_map_note}}", s, count=1, flags=re.S)

    # traffic
    t = s.index('aria-labelledby="traffic-title"')
    a = s.index('<p class="meta">', t)
    b = s.index('<h3 class="sub">Traffic Map</h3>', a)
    s = s[:a] + "{{traffic_body}}\n\n    " + s[b:]
    s = s.replace('aria-label="Map of traffic incidents, commuter rail delays, and construction"',
                  'aria-label="Map of traffic incidents and construction"')
    s = re.sub(r'<p class="stamp">Pins are approximate, placed from each location.*?</p>', "{{traffic_map_note}}", s, count=1, flags=re.S)

    # crime
    c = s.index('aria-labelledby="crime-title"')
    m = re.search(r'<p class="meta">.*?</p>', s[c:], flags=re.S)
    s = s[: c + m.start()] + "{{crime_meta}}" + s[c + m.end():]

    # rubric example cells (the 4 "Trip examples" cells in order Low, Medium, High, Very High)
    cells = list(re.finditer(r'(<td data-label="Trip examples">)(.*?)(</td>)', s, flags=re.S))
    assert len(cells) == 4, len(cells)
    for tok, mt in reversed(list(zip(["rubric_low", "rubric_medium", "rubric_high", "rubric_vhigh"], cells))):
        s = s[: mt.start(2)] + "{{%s}}" % tok + s[mt.end(2):]

    # footer
    f = s.index("<footer>") + len("<footer>")
    fe = s.index("</footer>")
    s = s[:f] + "\n    {{footer_sources}}\n  " + s[fe:]

    # JS data blocks
    s = replace_js_const(s, "tickerItems", "ticker_json", "[", "]")
    s = replace_js_const(s, "eventVenues", "event_venues_json", "[", "]")
    s = replace_js_const(s, "trafficItems", "traffic_items_json", "[", "]")
    s = replace_js_const(s, "crimeCounts", "crime_counts_json", "{", "}")

    # unrest pins become data-driven
    old = re.search(r"  L\.marker\(\[42\.4885, -71\.2175\], \{ icon: unrestIcon \}\)\s*\.bindPopup\(.*?\)\s*\.addTo\(unrestMap\);", s, flags=re.S)
    assert old, "unrest marker not found"
    s = s[: old.start()] + (
        "  const unrestPins = {{unrest_pins_json}};\n"
        "  unrestPins.forEach(p => {\n"
        "    L.marker([p.lat, p.lng], { icon: unrestIcon })\n"
        "      .bindPopup(`<strong>${p.name}</strong><br>${p.when}<br><span style=\"font-size:12px\">${p.note}</span>`)\n"
        "      .addTo(unrestMap);\n"
        "  });"
    ) + s[old.end():]

    # trafficBounds.fitBounds would throw on an empty list; guard it
    s = s.replace("trafficMap.fitBounds(trafficBounds, { padding: [20, 20] });",
                  "if (trafficBounds.length) { trafficMap.fitBounds(trafficBounds, { padding: [20, 20] }); }")

    # crime renderer: tolerate missing values instead of throwing
    s = s.replace("const c = crimeCounts[key];\n      document.getElementById(id).textContent = c.now.toLocaleString();",
                  "const c = crimeCounts[key];\n      if (!c || c.now == null || c.prev == null) {\n        document.getElementById(id).textContent = \"n/a\";\n        document.getElementById(chgId).textContent = \"Not available this run\";\n        return;\n      }\n      document.getElementById(id).textContent = c.now.toLocaleString();")
    s = s.replace('"As of " + crimeCounts.asOf + ". Source: BPD daily crime trends dashboard."',
                  'crimeCounts.stamp || ("As of " + crimeCounts.asOf)')

    # the schedule/source comment lines inside JS that mention manual entry
    s = re.sub(r"  // ---------- Crime counts \(manual entry\) ----------.*?\n(?=  const crimeCounts)",
               "  // ---------- Crime counts (filled by the pipeline) ----------\n", s, flags=re.S)

    open(dst, "w", encoding="utf-8").write(s)
    tokens = sorted(set(re.findall(r"\{\{(\w+)\}\}", s)))
    print("template written:", dst)
    print("placeholders (%d): %s" % (len(tokens), ", ".join(tokens)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
