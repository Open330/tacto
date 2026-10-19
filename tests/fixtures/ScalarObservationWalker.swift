// Frozen scalar reference for the observation batching differential test.
// It uses one AX read per field and does not construct a node packet. This is
// a checked-in test oracle, not a recovered historical build or benchmark.
func priorWalk(_ el: AXUIElement, depth: Int, out: inout [String],
               elements: inout [AXUIElement], visited: inout Int,
               truncated: inout Bool) {
    if depth > 25 { return }
    if visited >= maxTreeNodes { truncated = true; return }
    visited += 1
    let role = str(el, kAXRoleAttribute as String) ?? "?"
    var text = label(el, quoteValues: role == kAXWindowRole as String)
    if role == kAXWindowRole as String, let doc = str(el, "AXDocument"),
       let path = documentURLSyntaxPath(doc), !path.hasPrefix("/.file/"),
       let encoded = quotedLabelValue(path.precomposedStringWithCanonicalMapping) {
        text += (text.isEmpty ? "" : ", ") + "Document: \(encoded)"
    }
    if interactableRoles.contains(role) || !text.isEmpty {
        let index = elements.count
        elements.append(el)
        let enabled = (attr(el, kAXEnabledAttribute as String) as? Bool) ?? true
        let selected = (attr(el, kAXSelectedAttribute as String) as? Bool) == true
        let short = role.hasPrefix("AX") ? String(role.dropFirst(2)) : role
        let extra = secondaryActionsOf(el)
        let actions = extra.isEmpty ? "" : (text.isEmpty ? "  " : ", ")
            + "Secondary Actions: " + extra.map { String($0.dropFirst(2)) }.joined(separator: ", ")
        out.append(String(repeating: "  ", count: depth) + "\(index) \(short)"
            + (text.isEmpty ? "" : "  \(text)") + (enabled ? "" : " [disabled]")
            + (selected ? " [selected]" : "") + actions)
    }
    let children = (attr(el, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    for child in children.prefix(maxChildrenPerNode) {
        priorWalk(child, depth: depth+1, out: &out, elements: &elements,
                  visited: &visited, truncated: &truncated)
    }
    if children.count > maxChildrenPerNode {
        truncated = true
        out.append(String(repeating: "  ", count: depth+1)
            + "… (\(children.count-maxChildrenPerNode)개 더 있음)")
    }
}
