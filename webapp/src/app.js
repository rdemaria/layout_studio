/* Layout Studio browser application.
 *
 * This module renders the editors, coordinates import/export, keeps the
 * dependency tree and canvas selection synchronized, and performs every edit
 * through the strict geometry resolver before accepting it.
 */
(() => {
  "use strict";

  const M = globalThis.LayoutStudioModel;
  const Viewer = globalThis.LayoutStudioViewer?.LayoutViewer;
  if (!M || !Viewer) throw new Error("model.js and viewer.js must be loaded before app.js");

  const byId = (id) => document.getElementById(id);
  const dom = {
    status: byId("status"),
    statusMessage: byId("status-message"),
    layoutUrl: byId("layout-url"),
    loadUrlButton: byId("load-url-button"),
    importButton: byId("import-button"),
    fileInput: byId("file-input"),
    downloadButton: byId("download-button"),
    clearButton: byId("clear-button"),
    confirmClearButton: byId("confirm-clear-button"),
    helpButton: byId("help-button"),
    helpDialog: byId("help-dialog"),
    clearDialog: byId("clear-dialog"),
    curvesBody: byId("curves-body"),
    typesBody: byId("types-body"),
    objectsBody: byId("objects-body"),
    curveCount: byId("curve-count"),
    typeCount: byId("type-count"),
    objectCount: byId("object-count"),
    dependencyCount: byId("dependency-count"),
    viewerStatistics: byId("viewer-statistics"),
    dependencyTree: byId("dependency-tree"),
    expandAllButton: byId("expand-all-button"),
    collapseAllButton: byId("collapse-all-button"),
    worldPose: byId("world-pose"),
    viewerTooltip: byId("viewer-tooltip"),
    canvas: byId("layout-canvas"),
    addCurveButton: byId("add-curve-button"),
    addTypeButton: byId("add-type-button"),
    addObjectButton: byId("add-object-button"),
    fitLayoutButton: byId("fit-layout-button"),
    showCurves: byId("show-curves"),
    showObjects: byId("show-objects"),
    showBeamFrames: byId("show-beam-frames"),
  };

  const state = {
    layout: M.clone(M.DEFAULT_LAYOUT),
    resolver: null,
    selection: { kind: "object", name: "QF1" },
    current: { curve: "ring", type: "quadrupole", object: "QF1" },
    frameByType: { quadrupole: "survey_mark" },
    filters: { curve: "", type: "", object: "" },
    expanded: new Set(),
    collapsed: new Set(),
    statusTimer: 0,
    loading: false,
    viewerMode: "orbit",
  };

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replaceAll("`", "&#096;");
  }

  function option(value, label, selected = false, disabled = false) {
    return `<option value="${escapeAttribute(value)}"${selected ? " selected" : ""}${disabled ? " disabled" : ""}>${escapeHtml(label)}</option>`;
  }

  function options(values, selected, label = (value) => value) {
    return values.map((value) => option(value, label(value), value === selected)).join("");
  }

  function formatNumber(value, digits = 10) {
    if (!Number.isFinite(value)) return "";
    if (Object.is(value, -0)) value = 0;
    const absolute = Math.abs(value);
    if (absolute !== 0 && (absolute >= 1e7 || absolute < 1e-6)) return value.toExponential(8).replace(/0+e/, "e");
    return Number(value.toPrecision(digits)).toString();
  }

  function parseFinite(input, label) {
    const value = Number(input);
    if (!Number.isFinite(value)) throw new M.LayoutError(`${label} must be a finite number`);
    return value;
  }

  function colorFromName(name) {
    let hash = 2166136261;
    for (const character of name) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    const hue = ((hash >>> 0) % 360) / 360;
    const saturation = 0.52;
    const lightness = 0.62;
    const f = (n) => {
      const k = (n + hue * 12) % 12;
      const a = saturation * Math.min(lightness, 1 - lightness);
      return lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    };
    return `#${[f(0), f(8), f(4)].map((value) => Math.round(value * 255).toString(16).padStart(2, "0")).join("")}`;
  }

  function setStatus(kind, message, timeout = kind === "error" ? 9000 : 3600) {
    window.clearTimeout(state.statusTimer);
    dom.status.className = `status-pill status-${kind} visible`;
    dom.statusMessage.textContent = message;
    if (timeout > 0) {
      state.statusTimer = window.setTimeout(() => dom.status.classList.remove("visible"), timeout);
    }
  }

  function errorMessage(error) {
    return error instanceof Error ? error.message : String(error);
  }

  function firstKey(dictionary) {
    return Object.keys(dictionary)[0] ?? "";
  }

  function typeFrames(type) {
    return [...M.IMPLICIT_FRAMES, ...Object.keys(type?.frames ?? {})];
  }

  function ensureCurrentSelections() {
    const curves = Object.keys(state.layout.reference_curves);
    const types = Object.keys(state.layout.types);
    const objects = Object.keys(state.layout.objects);
    if (!curves.includes(state.current.curve)) state.current.curve = curves[0] ?? "";
    if (!types.includes(state.current.type)) state.current.type = types[0] ?? "";
    if (!objects.includes(state.current.object)) state.current.object = objects[0] ?? "";

    for (const typeName of types) {
      const frames = Object.keys(state.layout.types[typeName].frames);
      if (!frames.includes(state.frameByType[typeName])) state.frameByType[typeName] = frames[0] ?? "";
    }
    for (const typeName of Object.keys(state.frameByType)) {
      if (!types.includes(typeName)) delete state.frameByType[typeName];
    }

    if (state.selection?.kind === "curve" && !(state.selection.name in state.layout.reference_curves)) state.selection = null;
    if (state.selection?.kind === "object" && !(state.selection.name in state.layout.objects)) state.selection = null;
    if (state.selection?.kind === "frame") {
      const object = state.layout.objects[state.selection.object];
      if (!object || !typeFrames(state.layout.types[object.type]).includes(state.selection.name)) state.selection = null;
    }
  }

  function makeResolver(layout) {
    M.validateLayout(layout, { resolve: false });
    return new M.Resolver(layout).resolveAll();
  }

  function commit(mutator, successMessage = "Layout updated", { fit = false } = {}) {
    if (state.loading) return false;
    const next = M.clone(state.layout);
    try {
      mutator(next);
      const resolver = makeResolver(next);
      state.layout = next;
      state.resolver = resolver;
      ensureCurrentSelections();
      renderAll({ geometryChanged: true, fit });
      setStatus("success", successMessage);
      return true;
    } catch (error) {
      setStatus("error", errorMessage(error));
      return false;
    }
  }

  function loadLayout(layout, label, { fit = true } = {}) {
    const resolver = makeResolver(layout);
    state.layout = M.clone(layout);
    state.resolver = resolver;
    state.current.curve = firstKey(state.layout.reference_curves);
    state.current.type = firstKey(state.layout.types);
    state.current.object = firstKey(state.layout.objects);
    state.frameByType = {};
    state.selection = state.current.object ? { kind: "object", name: state.current.object } : state.current.curve ? { kind: "curve", name: state.current.curve } : null;
    state.expanded = new Set();
    ensureCurrentSelections();
    renderAll({ geometryChanged: true, fit });
    setStatus("success", label);
  }

  function dictionaryForKind(kind) {
    if (kind === "curve") return state.layout.reference_curves;
    if (kind === "type") return state.layout.types;
    return state.layout.objects;
  }

  function currentForKind(kind) {
    return state.current[kind];
  }

  function pickerOptions(kind) {
    const all = Object.keys(dictionaryForKind(kind));
    const filter = state.filters[kind].trim().toLocaleLowerCase();
    const selected = currentForKind(kind);
    let filtered = filter ? all.filter((name) => name.toLocaleLowerCase().includes(filter)) : all;
    if (selected && all.includes(selected) && !filtered.includes(selected)) filtered = [selected, ...filtered];
    return filtered;
  }

  function pickerHtml(kind) {
    const names = pickerOptions(kind);
    const selected = currentForKind(kind);
    return `
      <div class="entity-picker">
        <div class="entity-picker-main">
          <span class="search-icon" aria-hidden="true">⌕</span>
          <label class="sr-only" for="${kind}-filter">Filter ${kind}s</label>
          <input id="${kind}-filter" type="search" value="${escapeAttribute(state.filters[kind])}" placeholder="Filter ${kind}s…" data-filter-kind="${kind}" autocomplete="off" />
          <label class="sr-only" for="${kind}-picker">Selected ${kind}</label>
          <select id="${kind}-picker" data-select-kind="${kind}"${names.length ? "" : " disabled"}>
            ${names.length ? options(names, selected) : option("", `No ${kind}s`, true)}
          </select>
        </div>
        <span class="instances-note">${Object.keys(dictionaryForKind(kind)).length}</span>
      </div>`;
  }

  function updatePicker(kind) {
    const select = document.querySelector(`select[data-select-kind="${kind}"]`);
    if (!select) return;
    const names = pickerOptions(kind);
    select.innerHTML = names.length ? options(names, currentForKind(kind)) : option("", `No ${kind}s`, true);
    select.disabled = names.length === 0;
  }

  function contextAttributes(context) {
    return [
      `data-transform-kind="${escapeAttribute(context.kind)}"`,
      `data-entity-name="${escapeAttribute(context.entity)}"`,
      context.frame ? `data-frame-name="${escapeAttribute(context.frame)}"` : "",
    ].filter(Boolean).join(" ");
  }

  function transformationFor(layout, context) {
    if (context.kind === "curve-start") return layout.reference_curves[context.entity].starting_frame;
    if (context.kind === "type-magnetic") return layout.types[context.entity].magnetic_center;
    if (context.kind === "type-frame") return layout.types[context.entity].frames[context.frame];
    if (context.kind === "object-position") return layout.objects[context.entity].position;
    throw new Error(`unknown transformation context ${context.kind}`);
  }

  function allowedOperations(spec, context) {
    if (context.kind === "curve-start" && spec.reference.kind !== "curve") return M.OP_NAMES.filter((name) => name !== "ts");
    if (context.kind === "object-position" && spec.reference.kind !== "curve" && Object.keys(state.layout.reference_curves).length === 0) {
      return M.OP_NAMES.filter((name) => name !== "ts");
    }
    return M.OP_NAMES;
  }

  function operationsHtml(operations, context, spec = null) {
    const allowed = allowedOperations(spec ?? { reference: { kind: "world" } }, context);
    const rows = operations.map(([operation, rawValue], index) => {
      const displayValue = M.operationDisplayValue(operation, rawValue);
      return `
        <div class="operation-row" ${contextAttributes(context)} data-operation-index="${index}">
          <span class="operation-index">${String(index + 1).padStart(2, "0")}</span>
          <select aria-label="Operation ${index + 1}" data-action="operation-name">
            ${options(allowed, operation)}
          </select>
          <input type="number" step="${M.ROTATION_OPS.has(operation) ? "5" : "0.1"}" value="${escapeAttribute(formatNumber(displayValue, 12))}" aria-label="${operation} value" data-action="operation-value" />
          <span class="operation-unit">${M.operationUnit(operation) === "degree" ? "degree" : "metres"}</span>
          <div class="operation-actions">
            <button type="button" class="row-icon-button" data-action="operation-up" title="Move ${operation} up" aria-label="Move ${operation} up"${index === 0 ? " disabled" : ""}>↑</button>
            <button type="button" class="row-icon-button" data-action="operation-down" title="Move ${operation} down" aria-label="Move ${operation} down"${index === operations.length - 1 ? " disabled" : ""}>↓</button>
            <button type="button" class="row-icon-button danger" data-action="operation-remove" title="Remove ${operation}" aria-label="Remove ${operation}">×</button>
          </div>
        </div>`;
    }).join("");
    return `
      <div class="operation-editor" ${contextAttributes(context)}>
        <div class="operation-editor-header">
          <span>Ordered operations</span>
          <button type="button" class="button button-outline button-small" data-action="operation-add">+ Operation</button>
        </div>
        <div class="operation-list">${rows || '<p class="inline-empty">No transformations — frames coincide.</p>'}</div>
      </div>`;
  }

  function defaultObjectReference(excludeName = "") {
    const name = Object.keys(state.layout.objects).find((candidate) => candidate !== excludeName) ?? Object.keys(state.layout.objects)[0] ?? "";
    if (!name) return null;
    return { kind: "object_frame", object: name, frame: "center" };
  }

  function referenceHtml(spec, context) {
    const reference = spec.reference;
    const curves = Object.keys(state.layout.reference_curves);
    const ownerObject = context.kind === "object-position" ? context.entity : "";
    const objects = Object.keys(state.layout.objects).filter((name) => !ownerObject || name !== ownerObject);
    const kinds = ["world"];
    if (curves.length) kinds.push("curve");
    if (objects.length) kinds.push("object_frame");
    const kindLabel = { world: "World", curve: "Curve", object_frame: "Object frame" };
    const hasTs = spec.transformation.some(([name]) => name === "ts");
    let detail = "";
    if (reference.kind === "curve") {
      detail = `
        <div class="field"><label>Curve</label><select data-action="reference-curve" ${contextAttributes(context)}>${options(curves, reference.curve)}</select></div>`;
    } else if (reference.kind === "object_frame") {
      const referencedObject = state.layout.objects[reference.object];
      const frames = referencedObject ? typeFrames(state.layout.types[referencedObject.type]) : ["center"];
      detail = `
        <div class="field"><label>Object</label><select data-action="reference-object" ${contextAttributes(context)}>${options(objects, reference.object)}</select></div>
        <div class="field"><label>Frame</label><select data-action="reference-frame" ${contextAttributes(context)}>${options(frames, reference.frame)}</select></div>`;
    } else {
      detail = `<div class="field"><label>Reference frame</label><input type="text" value="World" disabled /></div>`;
    }

    const stationCurve = context.kind === "object-position" && reference.kind !== "curve" && hasTs
      ? `<div class="field"><label>Reference curve for ts</label><select data-action="station-curve" ${contextAttributes(context)}>${options(curves, spec.reference_curve)}</select></div>`
      : "";

    return `
      <div class="reference-editor">
        <div class="reference-grid${reference.kind === "object_frame" ? " three" : ""}">
          <div class="field"><label>Reference</label><select data-action="reference-kind" ${contextAttributes(context)}>${kinds.map((kind) => option(kind, kindLabel[kind], kind === reference.kind)).join("")}</select></div>
          ${detail}
          ${stationCurve}
        </div>
        ${context.kind === "object-position" && reference.kind !== "curve" && hasTs ? '<p class="reference-help">ts first finds the closest normal plane of the selected reference curve containing the referenced origin.</p>' : ""}
      </div>`;
  }


  function transformationEditorHtml(spec, context) {
    return `${referenceHtml(spec, context)}${operationsHtml(spec.transformation, context, spec)}`;
  }

  function renderCurves() {
    const curves = state.layout.reference_curves;
    const name = state.current.curve;
    dom.curveCount.textContent = Object.keys(curves).length;
    if (!name || !curves[name]) {
      dom.curvesBody.innerHTML = `<div class="empty-state"><strong>No reference curves</strong><p>Create a curve to define a spatial backbone.</p><button class="button button-primary button-small" type="button" data-action="add-curve">+ Curve</button></div>`;
      return;
    }
    const curve = curves[name];
    const segmentRows = curve.segments.map((segment, index) => `
      <tr>
        <td>${String(index + 1).padStart(2, "0")}</td>
        <td><input type="number" min="1e-12" step="0.1" value="${escapeAttribute(formatNumber(segment[0], 12))}" data-action="segment-value" data-curve-name="${escapeAttribute(name)}" data-segment-index="${index}" data-segment-field="0" aria-label="Segment ${index + 1} length" /></td>
        <td><input type="number" step="5" value="${escapeAttribute(formatNumber(segment[1] * M.RAD, 12))}" data-action="segment-value" data-curve-name="${escapeAttribute(name)}" data-segment-index="${index}" data-segment-field="1" aria-label="Segment ${index + 1} angle in degrees" /></td>
        <td><input type="number" step="5" value="${escapeAttribute(formatNumber(segment[2] * M.RAD, 12))}" data-action="segment-value" data-curve-name="${escapeAttribute(name)}" data-segment-index="${index}" data-segment-field="2" aria-label="Segment ${index + 1} roll in degrees" /></td>
        <td><button type="button" class="row-icon-button danger" data-action="remove-segment" data-curve-name="${escapeAttribute(name)}" data-segment-index="${index}" aria-label="Remove segment ${index + 1}"${curve.segments.length === 1 ? " disabled" : ""}>×</button></td>
      </tr>`).join("");

    dom.curvesBody.innerHTML = `
      ${pickerHtml("curve")}
      <section class="editor-section">
        <div class="entity-name-row">
          <div class="field"><label for="curve-name-input">Curve name</label><input id="curve-name-input" type="text" value="${escapeAttribute(name)}" autocomplete="off" /></div>
          <button class="button button-outline button-small" type="button" data-action="rename-curve">Rename</button>
          <button class="button button-ghost button-small" type="button" data-action="remove-curve">Remove</button>
        </div>
        <div class="field-grid">
          <div class="field"><label>Curve color</label><div class="color-field"><input type="color" value="${escapeAttribute(curve.color)}" data-action="curve-color" data-curve-name="${escapeAttribute(name)}" aria-label="Curve color" /><input type="text" value="${escapeAttribute(curve.color)}" data-action="curve-color" data-curve-name="${escapeAttribute(name)}" aria-label="Curve color code" /></div></div>
          <div class="field"><label>Defined path length</label><input type="text" value="${formatNumber(curve.segments.reduce((sum, segment) => sum + segment[0], 0), 12)} m" disabled /></div>
        </div>
      </section>
      <section class="editor-section soft">
        <div class="section-heading"><div class="section-heading-copy"><h3>Starting frame</h3><p>Reference and ordered transformation</p></div></div>
        ${transformationEditorHtml(curve.starting_frame, { kind: "curve-start", entity: name })}
      </section>
      <section class="editor-section">
        <div class="section-heading"><div class="section-heading-copy"><h3>Segments <span class="count">${curve.segments.length}</span></h3><p>length [m] · angle [degree] · roll [degree]</p></div><div class="section-tools"><button class="button button-outline button-small" type="button" data-action="add-segment">+ Segment</button></div></div>
        <div class="segment-table-wrap"><table class="segment-table"><thead><tr><th>#</th><th>Length [m]</th><th>Angle [°]</th><th>Roll [°]</th><th></th></tr></thead><tbody>${segmentRows}</tbody></table></div>
      </section>`;
  }

  function shapeEditorHtml(name, type) {
    const shape = M.typeShape(type);
    const common = `
      <div class="field"><label>dz [m]</label><input type="number" min="1e-12" step="0.1" value="${escapeAttribute(formatNumber(shape.dz, 12))}" data-action="type-shape-value" data-type-name="${escapeAttribute(name)}" data-shape-field="dz" /></div>
      <div class="field"><label>Curvature [1/m]</label><input type="number" step="0.01" value="${escapeAttribute(formatNumber(shape.curvature, 12))}" data-action="type-shape-value" data-type-name="${escapeAttribute(name)}" data-shape-field="curvature" /></div>
      <div class="field"><label>Roll [degree]</label><input type="number" step="5" value="${escapeAttribute(formatNumber(shape.roll * M.RAD, 12))}" data-action="type-shape-value" data-type-name="${escapeAttribute(name)}" data-shape-field="roll" /></div>`;
    const dimensions = shape.primitive === "box"
      ? `<div class="field"><label>dx [m]</label><input type="number" min="1e-12" step="0.1" value="${escapeAttribute(formatNumber(shape.dx, 12))}" data-action="type-shape-value" data-type-name="${escapeAttribute(name)}" data-shape-field="dx" /></div><div class="field"><label>dy [m]</label><input type="number" min="1e-12" step="0.1" value="${escapeAttribute(formatNumber(shape.dy, 12))}" data-action="type-shape-value" data-type-name="${escapeAttribute(name)}" data-shape-field="dy" /></div>`
      : `<div class="field"><label>Radius [m]</label><input type="number" min="1e-12" step="0.1" value="${escapeAttribute(formatNumber(shape.radius, 12))}" data-action="type-shape-value" data-type-name="${escapeAttribute(name)}" data-shape-field="radius" /></div>`;
    return `
      <div class="field-grid three">
        <div class="field"><label>Primitive</label><select data-action="type-primitive" data-type-name="${escapeAttribute(name)}">${option("box", "Box", shape.primitive === "box")}${option("cylinder", "Cylinder", shape.primitive === "cylinder")}</select></div>
        ${dimensions}${common}
      </div>
      <p class="shape-hint">s = 0 at every object center. dz is centerline arc length. Positive curvature at zero roll bends toward −x; positive roll rotates the bend toward −y.</p>`;
  }

  function renderTypes() {
    const types = state.layout.types;
    const name = state.current.type;
    dom.typeCount.textContent = Object.keys(types).length;
    if (!name || !types[name]) {
      dom.typesBody.innerHTML = `<div class="empty-state"><strong>No reusable types</strong><p>Create a type before adding positioned objects.</p><button class="button button-primary button-small" type="button" data-action="add-type">+ Type</button></div>`;
      return;
    }
    const type = types[name];
    const instances = Object.entries(state.layout.objects).filter(([, object]) => object.type === name).map(([objectName]) => objectName);
    const frameNames = Object.keys(type.frames);
    const frameName = state.frameByType[name] ?? "";
    const frameEditor = frameName && type.frames[frameName]
      ? `
        <div class="frame-editor">
          <div class="frame-editor-header">
            <div class="field"><label for="frame-name-input">Frame name</label><input id="frame-name-input" type="text" value="${escapeAttribute(frameName)}" autocomplete="off" /></div>
            <button class="button button-outline button-small" type="button" data-action="rename-frame">Rename</button>
            <button class="button button-ghost button-small" type="button" data-action="remove-frame">Remove</button>
          </div>
          <p class="implicit-reference">Relative to the object center · ts follows the type curve · tt moves straight along the current tangent.</p>
          ${operationsHtml(type.frames[frameName].transformation, { kind: "type-frame", entity: name, frame: frameName })}
        </div>`
      : '<p class="inline-empty">No named frames. The four magnetic/center frames remain implicit.</p>';

    dom.typesBody.innerHTML = `
      ${pickerHtml("type")}
      <section class="editor-section">
        <div class="entity-name-row">
          <div class="field"><label for="type-name-input">Type name</label><input id="type-name-input" type="text" value="${escapeAttribute(name)}" autocomplete="off" /></div>
          <button class="button button-outline button-small" type="button" data-action="rename-type">Rename</button>
          <button class="button button-ghost button-small" type="button" data-action="remove-type">Remove</button>
        </div>
        <div class="field-grid">
          <div class="field"><label>Instances</label><input type="text" value="${escapeAttribute(instances.length ? `${instances.length}: ${instances.slice(0, 4).join(", ")}${instances.length > 4 ? "…" : ""}` : "0")}" disabled /></div>
          <div class="field"><label>Type color</label><div class="color-field"><input type="color" value="${escapeAttribute(type.color)}" data-action="type-color" data-type-name="${escapeAttribute(name)}" aria-label="Type color" /><input type="text" value="${escapeAttribute(type.color)}" data-action="type-color" data-type-name="${escapeAttribute(name)}" aria-label="Type color code" /></div></div>
        </div>
      </section>
      <section class="editor-section soft">
        <div class="section-heading"><div class="section-heading-copy"><h3>Shape</h3><p>s = 0 at every object center</p></div></div>
        ${shapeEditorHtml(name, type)}
      </section>
      <section class="editor-section">
        <div class="section-heading"><div class="section-heading-copy"><h3>Magnetic axis</h3><p>Beam entry / exit</p></div></div>
        <div class="field"><label>Magnetic length [m]</label><input type="number" min="1e-12" step="0.1" value="${escapeAttribute(formatNumber(type.magnetic_length, 12))}" data-action="magnetic-length" data-type-name="${escapeAttribute(name)}" /></div>
        <p class="field-help">Magnetic center is defined relative to the object center. Entry and exit are derived at −Lmag/2 and +Lmag/2 with planes normal to the tangent.</p>
        ${operationsHtml(type.magnetic_center.transformation, { kind: "type-magnetic", entity: name })}
      </section>
      <section class="editor-section soft">
        <div class="section-heading">
          <div class="section-heading-copy"><h3>Named frames <span class="count">${frameNames.length}</span></h3><p>Implicit reference: object center</p></div>
          <div class="section-tools"><button class="button button-outline button-small" type="button" data-action="add-frame">+ Frame</button></div>
        </div>
        ${frameNames.length ? `<div class="field"><label>Frame</label><select data-action="select-frame">${options(frameNames, frameName)}</select></div>` : ""}
        ${frameEditor}
      </section>`;
  }

  function renderObjects() {
    const objects = state.layout.objects;
    const name = state.current.object;
    dom.objectCount.textContent = Object.keys(objects).length;
    dom.addObjectButton.disabled = Object.keys(state.layout.types).length === 0;
    if (!name || !objects[name]) {
      dom.objectsBody.innerHTML = `<div class="empty-state"><strong>No positioned objects</strong><p>${Object.keys(state.layout.types).length ? "Create an instance of a reusable type." : "Create a type first."}</p><button class="button button-primary button-small" type="button" data-action="add-object"${Object.keys(state.layout.types).length ? "" : " disabled"}>+ Object</button></div>`;
      return;
    }
    const object = objects[name];
    const type = state.layout.types[object.type];
    const targets = typeFrames(type);
    dom.objectsBody.innerHTML = `
      ${pickerHtml("object")}
      <section class="editor-section">
        <div class="entity-name-row">
          <div class="field"><label for="object-name-input">Object name</label><input id="object-name-input" type="text" value="${escapeAttribute(name)}" autocomplete="off" /></div>
          <button class="button button-outline button-small" type="button" data-action="rename-object">Rename</button>
          <button class="button button-ghost button-small" type="button" data-action="remove-object">Remove</button>
        </div>
        <div class="field-grid">
          <div class="field"><label>Type</label><select data-action="object-type">${options(Object.keys(state.layout.types), object.type)}</select></div>
          <div class="field"><label>Target frame</label><select data-action="object-target">${options(targets, object.position.target)}</select></div>
        </div>
        <div class="section-tools"><button class="button button-secondary button-small" type="button" data-action="edit-object-type">Edit type ${escapeHtml(object.type)}</button></div>
      </section>
      <section class="editor-section soft">
        <div class="section-heading"><div class="section-heading-copy"><h3>Position</h3><p>Place target at (reference, transformation)</p></div></div>
        ${transformationEditorHtml(object.position, { kind: "object-position", entity: name })}
      </section>`;
  }

  function selectionFrame() {
    if (!state.selection || !state.resolver) return null;
    if (state.selection.kind === "curve") {
      const pose = state.resolver.curveFrame(state.selection.name, 0);
      return { pose, title: `Curve ${state.selection.name}`, subtitle: "starting frame" };
    }
    if (state.selection.kind === "object") {
      const object = state.layout.objects[state.selection.name];
      if (!object) return null;
      const pose = state.resolver.objectCenter(state.selection.name);
      return { pose, title: `Object ${state.selection.name}`, subtitle: `${object.type} · center` };
    }
    if (state.selection.kind === "frame") {
      const object = state.layout.objects[state.selection.object];
      if (!object) return null;
      const pose = state.resolver.objectFrame(state.selection.object, state.selection.name);
      return { pose, title: `Frame ${state.selection.object} → ${state.selection.name}`, subtitle: object.type };
    }
    return null;
  }

  function renderPose() {
    try {
      const selected = selectionFrame();
      if (!selected) {
        dom.worldPose.innerHTML = '<div class="pose-empty">Select a curve, object or frame to inspect its world pose.</div>';
        return;
      }
      const summary = M.frameSummary(selected.pose.frame);
      const stations = Object.entries(selected.pose.stations ?? {});
      const values = [
        ["X", `${formatNumber(summary.x, 11)} m`],
        ["Y", `${formatNumber(summary.y, 11)} m`],
        ["Z", `${formatNumber(summary.z, 11)} m`],
        ["theta", `${formatNumber(summary.theta * M.RAD, 10)}°`],
        ["phi", `${formatNumber(summary.phi * M.RAD, 10)}°`],
        ["psi", `${formatNumber(summary.psi * M.RAD, 10)}°`],
        ...stations.slice(0, 3).map(([curve, station]) => [`s · ${curve}`, `${formatNumber(station, 11)} m`]),
      ];
      dom.worldPose.innerHTML = `
        <div class="pose-heading"><h3>World pose</h3><p>${escapeHtml(selected.title)} · ${escapeHtml(selected.subtitle)}</p></div>
        <div class="pose-values">${values.map(([label, value]) => `<div class="pose-value"><span>${escapeHtml(label)}</span><strong title="${escapeAttribute(value)}">${escapeHtml(value)}</strong></div>`).join("")}</div>`;
    } catch (error) {
      dom.worldPose.innerHTML = `<div class="pose-empty">${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  function isSelectedNode(node) {
    if (!state.selection) return false;
    if (node.kind === "curve") return state.selection.kind === "curve" && state.selection.name === node.name;
    if (node.kind === "object") return (state.selection.kind === "object" && state.selection.name === node.name) || (state.selection.kind === "frame" && state.selection.object === node.name);
    return false;
  }

  function dependencyNodeHtml(graph, id, relation, ancestors = new Set()) {
    const node = graph.nodes.get(id);
    if (!node) return "";
    const children = graph.children.get(id) ?? [];
    const expanded = state.expanded.has(id);
    const cycle = ancestors.has(id);
    const nextAncestors = new Set(ancestors).add(id);
    const selected = isSelectedNode(node);
    const icon = node.kind === "world" ? "◎" : node.kind === "curve" ? "⌁" : "◇";
    const relationText = relation ?? (node.kind === "world" ? "global frame" : "");
    const childHtml = expanded && !cycle
      ? `<ul role="group">${children.map((edge) => dependencyNodeHtml(graph, edge.id, edge.relation, nextAncestors)).join("")}</ul>`
      : "";
    return `
      <li class="dependency-item${cycle ? " dependency-cycle" : ""}" role="treeitem" aria-expanded="${children.length ? expanded : false}">
        <div class="dependency-row${selected ? " selected" : ""}">
          ${children.length && !cycle ? `<button type="button" class="dependency-disclosure" data-action="toggle-dependency" data-node-id="${escapeAttribute(id)}" aria-label="${expanded ? "Collapse" : "Expand"} ${escapeAttribute(node.name)}" aria-expanded="${expanded}"></button>` : '<span class="dependency-disclosure-spacer"></span>'}
          <span class="dependency-kind-icon ${escapeAttribute(node.kind)}" aria-hidden="true">${icon}</span>
          <button type="button" class="dependency-node" data-action="select-dependency" data-node-id="${escapeAttribute(id)}">
            <span class="dependency-node-name">${escapeHtml(node.name)}</span>
            <span class="dependency-node-kind">${escapeHtml(node.kind)}</span>
            <span class="dependency-relation">${escapeHtml(cycle ? "cycle" : relationText)}</span>
          </button>
        </div>
        ${childHtml}
      </li>`;
  }

  function renderDependencies() {
    const graph = M.buildDependencyGraph(state.layout);
    const count = graph.nodes.size - 1;
    dom.dependencyCount.textContent = count;
    const expandable = [...graph.nodes.keys()].filter((id) => (graph.children.get(id) ?? []).length > 0);
    dom.expandAllButton.disabled = expandable.length === 0 || expandable.every((id) => state.expanded.has(id));
    dom.collapseAllButton.disabled = state.expanded.size === 0;
    dom.dependencyTree.innerHTML = dependencyNodeHtml(graph, "world", null) || '<li class="dependency-empty">No dependencies</li>';
  }

  function renderStatistics() {
    const curveCount = Object.keys(state.layout.reference_curves).length;
    const typeCount = Object.keys(state.layout.types).length;
    const objectCount = Object.keys(state.layout.objects).length;
    const frameCount = Object.values(state.layout.objects).reduce((sum, object) => sum + Object.keys(state.layout.types[object.type]?.frames ?? {}).length, 0);
    dom.viewerStatistics.textContent = `${curveCount} curves · ${typeCount} types · ${objectCount} objects · ${frameCount} named frames`;
  }

  function renderCollapsedState() {
    for (const card of document.querySelectorAll(".card")) {
      const collapsed = state.collapsed.has(card.id);
      card.classList.toggle("collapsed", collapsed);
      const button = card.querySelector(`[data-toggle-card="${CSS.escape(card.id)}"]`);
      if (button) {
        button.textContent = collapsed ? "Show" : "Hide";
        button.setAttribute("aria-expanded", String(!collapsed));
      }
    }
  }

  function renderAll({ geometryChanged = false, fit = false } = {}) {
    ensureCurrentSelections();
    renderCurves();
    renderTypes();
    renderObjects();
    renderStatistics();
    renderDependencies();
    renderPose();
    renderCollapsedState();
    if (geometryChanged) viewer.setLayout(state.layout, state.resolver, { fit });
    viewer.setSelection(state.selection);
  }

  function selectEntity(selection, { reveal = true } = {}) {
    state.selection = selection;
    if (selection?.kind === "curve") state.current.curve = selection.name;
    if (selection?.kind === "object") {
      state.current.object = selection.name;
      const object = state.layout.objects[selection.name];
      if (object) state.current.type = object.type;
    }
    if (selection?.kind === "frame") {
      state.current.object = selection.object;
      const object = state.layout.objects[selection.object];
      if (object) {
        state.current.type = object.type;
        if (!M.IMPLICIT_FRAMES.includes(selection.name)) state.frameByType[object.type] = selection.name;
      }
    }
    renderAll();
    if (reveal && selection) {
      const cardId = selection.kind === "curve" ? "curves-card" : "objects-card";
      if (state.collapsed.delete(cardId)) renderCollapsedState();
    }
  }

  function referenceDescriptionsForCurve(name) {
    const uses = [];
    for (const [curveName, curve] of Object.entries(state.layout.reference_curves)) {
      if (curveName !== name && curve.starting_frame.reference.kind === "curve" && curve.starting_frame.reference.curve === name) uses.push(`curve ${curveName}`);
    }
    for (const [objectName, object] of Object.entries(state.layout.objects)) {
      if (object.position.reference.kind === "curve" && object.position.reference.curve === name) uses.push(`object ${objectName}`);
      if (object.position.reference_curve === name) uses.push(`station curve of ${objectName}`);
    }
    return uses;
  }

  function referenceDescriptionsForObject(name) {
    const uses = [];
    for (const [curveName, curve] of Object.entries(state.layout.reference_curves)) {
      if (curve.starting_frame.reference.kind === "object_frame" && curve.starting_frame.reference.object === name) uses.push(`curve ${curveName}`);
    }
    for (const [objectName, object] of Object.entries(state.layout.objects)) {
      if (objectName !== name && object.position.reference.kind === "object_frame" && object.position.reference.object === name) uses.push(`object ${objectName}`);
    }
    return uses;
  }

  function frameUses(typeName, frameName) {
    const instances = new Set(Object.entries(state.layout.objects).filter(([, object]) => object.type === typeName).map(([name]) => name));
    const uses = [];
    for (const [objectName, object] of Object.entries(state.layout.objects)) {
      if (object.type === typeName && object.position.target === frameName) uses.push(`target of ${objectName}`);
      if (object.position.reference.kind === "object_frame" && instances.has(object.position.reference.object) && object.position.reference.frame === frameName) uses.push(`reference of ${objectName}`);
    }
    for (const [curveName, curve] of Object.entries(state.layout.reference_curves)) {
      const reference = curve.starting_frame.reference;
      if (reference.kind === "object_frame" && instances.has(reference.object) && reference.frame === frameName) uses.push(`starting frame of ${curveName}`);
    }
    return uses;
  }

  function requireNewName(inputId, dictionary, oldName, label, reserved = []) {
    const value = byId(inputId)?.value.trim() ?? "";
    if (!value) throw new M.LayoutError(`${label} name cannot be empty`);
    if (reserved.includes(value)) throw new M.LayoutError(`${JSON.stringify(value)} is a reserved ${label} name`);
    if (value !== oldName && value in dictionary) throw new M.LayoutError(`${label} ${JSON.stringify(value)} already exists`);
    return value;
  }

  function parseContext(element) {
    const holder = element.closest("[data-transform-kind]");
    if (!holder) throw new Error("missing transformation context");
    return {
      kind: holder.dataset.transformKind,
      entity: holder.dataset.entityName,
      frame: holder.dataset.frameName || undefined,
    };
  }

  function operationIndex(element) {
    const row = element.closest("[data-operation-index]");
    if (!row) throw new Error("missing operation index");
    return Number(row.dataset.operationIndex);
  }

  function handleOperationClick(action, element) {
    const context = parseContext(element);
    if (action === "operation-add") {
      commit((layout) => {
        const spec = transformationFor(layout, context);
        const allowed = allowedOperations(spec, context);
        spec.transformation.push([allowed[0] ?? "tx", 0]);
      }, "Operation added");
      return;
    }
    const index = operationIndex(element);
    commit((layout) => {
      const operations = transformationFor(layout, context).transformation;
      if (action === "operation-remove") operations.splice(index, 1);
      if (action === "operation-up" && index > 0) [operations[index - 1], operations[index]] = [operations[index], operations[index - 1]];
      if (action === "operation-down" && index + 1 < operations.length) [operations[index + 1], operations[index]] = [operations[index], operations[index + 1]];
      if (context.kind === "object-position" && !operations.some(([name]) => name === "ts")) delete transformationFor(layout, context).reference_curve;
    }, action === "operation-remove" ? "Operation removed" : "Operation reordered");
  }

  function handleEntityAction(action) {
    if (action === "add-curve") {
      const name = M.uniqueName(state.layout.reference_curves, "curve");
      commit((layout) => {
        layout.reference_curves[name] = { color: colorFromName(name), starting_frame: { reference: { kind: "world" }, transformation: [] }, segments: [[1, 0, 0]] };
        state.current.curve = name;
        state.selection = { kind: "curve", name };
      }, `Curve ${name} added`);
      return;
    }
    if (action === "add-type") {
      const name = M.uniqueName(state.layout.types, "type");
      commit((layout) => {
        layout.types[name] = { shape: ["box", 1, 1, 1, 0, 0], color: colorFromName(name), magnetic_center: { transformation: [] }, magnetic_length: 1, frames: {} };
        state.current.type = name;
      }, `Type ${name} added`);
      return;
    }
    if (action === "add-object") {
      const typeName = state.current.type || firstKey(state.layout.types);
      if (!typeName) return setStatus("error", "Create a type before adding an object");
      const name = M.uniqueName(state.layout.objects, "object");
      commit((layout) => {
        layout.objects[name] = { type: typeName, position: { target: "center", reference: { kind: "world" }, transformation: [] } };
        state.current.object = name;
        state.selection = { kind: "object", name };
      }, `Object ${name} added`);
    }
  }

  function handleRename(action) {
    try {
      if (action === "rename-curve") {
        const oldName = state.current.curve;
        const newName = requireNewName("curve-name-input", state.layout.reference_curves, oldName, "curve");
        if (newName === oldName) return setStatus("success", "Curve name unchanged");
        commit((layout) => {
          M.renameKey(layout.reference_curves, oldName, newName);
          M.walkReferences(layout, (reference) => { if (reference.kind === "curve" && reference.curve === oldName) reference.curve = newName; });
          for (const object of Object.values(layout.objects)) if (object.position.reference_curve === oldName) object.position.reference_curve = newName;
          state.current.curve = newName;
          if (state.selection?.kind === "curve" && state.selection.name === oldName) state.selection.name = newName;
        }, `Curve renamed to ${newName}`);
      }
      if (action === "rename-type") {
        const oldName = state.current.type;
        const newName = requireNewName("type-name-input", state.layout.types, oldName, "type");
        if (newName === oldName) return setStatus("success", "Type name unchanged");
        commit((layout) => {
          M.renameKey(layout.types, oldName, newName);
          for (const object of Object.values(layout.objects)) if (object.type === oldName) object.type = newName;
          state.current.type = newName;
          state.frameByType[newName] = state.frameByType[oldName] ?? "";
          delete state.frameByType[oldName];
        }, `Type renamed to ${newName}`);
      }
      if (action === "rename-object") {
        const oldName = state.current.object;
        const newName = requireNewName("object-name-input", state.layout.objects, oldName, "object");
        if (newName === oldName) return setStatus("success", "Object name unchanged");
        commit((layout) => {
          M.renameKey(layout.objects, oldName, newName);
          M.walkReferences(layout, (reference) => { if (reference.kind === "object_frame" && reference.object === oldName) reference.object = newName; });
          state.current.object = newName;
          if (state.selection?.kind === "object" && state.selection.name === oldName) state.selection.name = newName;
          if (state.selection?.kind === "frame" && state.selection.object === oldName) state.selection.object = newName;
        }, `Object renamed to ${newName}`);
      }
      if (action === "rename-frame") {
        const typeName = state.current.type;
        const oldName = state.frameByType[typeName];
        const newName = requireNewName("frame-name-input", state.layout.types[typeName].frames, oldName, "frame", M.IMPLICIT_FRAMES);
        if (newName === oldName) return setStatus("success", "Frame name unchanged");
        commit((layout) => {
          const type = layout.types[typeName];
          M.renameKey(type.frames, oldName, newName);
          const instances = new Set(Object.entries(layout.objects).filter(([, object]) => object.type === typeName).map(([name]) => name));
          for (const object of Object.values(layout.objects)) {
            if (object.type === typeName && object.position.target === oldName) object.position.target = newName;
            const reference = object.position.reference;
            if (reference.kind === "object_frame" && instances.has(reference.object) && reference.frame === oldName) reference.frame = newName;
          }
          for (const curve of Object.values(layout.reference_curves)) {
            const reference = curve.starting_frame.reference;
            if (reference.kind === "object_frame" && instances.has(reference.object) && reference.frame === oldName) reference.frame = newName;
          }
          state.frameByType[typeName] = newName;
          if (state.selection?.kind === "frame" && instances.has(state.selection.object) && state.selection.name === oldName) state.selection.name = newName;
        }, `Frame renamed to ${newName}`);
      }
    } catch (error) {
      setStatus("error", errorMessage(error));
    }
  }

  function handleRemove(action) {
    if (action === "remove-curve") {
      const name = state.current.curve;
      const uses = referenceDescriptionsForCurve(name);
      if (uses.length) return setStatus("error", `Curve ${name} is still used by ${uses.slice(0, 4).join(", ")}${uses.length > 4 ? "…" : ""}`);
      commit((layout) => { delete layout.reference_curves[name]; }, `Curve ${name} removed`);
    }
    if (action === "remove-type") {
      const name = state.current.type;
      const uses = Object.entries(state.layout.objects).filter(([, object]) => object.type === name).map(([objectName]) => objectName);
      if (uses.length) return setStatus("error", `Type ${name} still has ${uses.length} instance${uses.length === 1 ? "" : "s"}: ${uses.slice(0, 4).join(", ")}${uses.length > 4 ? "…" : ""}`);
      commit((layout) => { delete layout.types[name]; }, `Type ${name} removed`);
    }
    if (action === "remove-object") {
      const name = state.current.object;
      const uses = referenceDescriptionsForObject(name);
      if (uses.length) return setStatus("error", `Object ${name} is still referenced by ${uses.slice(0, 4).join(", ")}${uses.length > 4 ? "…" : ""}`);
      commit((layout) => { delete layout.objects[name]; }, `Object ${name} removed`);
    }
    if (action === "remove-frame") {
      const typeName = state.current.type;
      const frameName = state.frameByType[typeName];
      const uses = frameUses(typeName, frameName);
      if (uses.length) return setStatus("error", `Frame ${typeName}.${frameName} is still used by ${uses.slice(0, 4).join(", ")}${uses.length > 4 ? "…" : ""}`);
      commit((layout) => { delete layout.types[typeName].frames[frameName]; }, `Frame ${frameName} removed`);
    }
  }

  function handleClick(event) {
    const toggle = event.target.closest("[data-toggle-card]");
    if (toggle) {
      const cardId = toggle.dataset.toggleCard;
      if (state.collapsed.has(cardId)) state.collapsed.delete(cardId);
      else state.collapsed.add(cardId);
      renderCollapsedState();
      return;
    }
    const element = event.target.closest("[data-action]");
    if (!element) return;
    const action = element.dataset.action;
    if (["add-curve", "add-type", "add-object"].includes(action)) return handleEntityAction(action);
    if (["rename-curve", "rename-type", "rename-object", "rename-frame"].includes(action)) return handleRename(action);
    if (["remove-curve", "remove-type", "remove-object", "remove-frame"].includes(action)) return handleRemove(action);
    if (["operation-add", "operation-remove", "operation-up", "operation-down"].includes(action)) return handleOperationClick(action, element);

    if (action === "add-segment") {
      const name = state.current.curve;
      commit((layout) => layout.reference_curves[name].segments.push([1, 0, 0]), "Segment added");
    }
    if (action === "remove-segment") {
      const name = element.dataset.curveName;
      const index = Number(element.dataset.segmentIndex);
      commit((layout) => layout.reference_curves[name].segments.splice(index, 1), "Segment removed");
    }
    if (action === "add-frame") {
      const typeName = state.current.type;
      const frameName = M.uniqueName(state.layout.types[typeName].frames, "frame");
      commit((layout) => {
        layout.types[typeName].frames[frameName] = { transformation: [] };
        state.frameByType[typeName] = frameName;
      }, `Frame ${frameName} added`);
    }
    if (action === "edit-object-type") {
      const object = state.layout.objects[state.current.object];
      if (object) {
        state.current.type = object.type;
        state.collapsed.delete("types-card");
        renderAll();
        byId("types-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
    if (action === "toggle-dependency") {
      const id = element.dataset.nodeId;
      if (state.expanded.has(id)) state.expanded.delete(id);
      else state.expanded.add(id);
      renderDependencies();
    }
    if (action === "select-dependency") {
      const id = element.dataset.nodeId;
      if (id === "world") selectEntity(null);
      else if (id.startsWith("curve:")) selectEntity({ kind: "curve", name: id.slice(6) });
      else if (id.startsWith("object:")) selectEntity({ kind: "object", name: id.slice(7) });
    }
  }

  function handleReferenceChange(action, element) {
    const context = parseContext(element);
    commit((layout) => {
      const spec = transformationFor(layout, context);
      if (action === "reference-kind") {
        const kind = element.value;
        if (kind === "world") spec.reference = { kind: "world" };
        if (kind === "curve") {
          spec.reference = { kind: "curve", curve: firstKey(layout.reference_curves) };
          delete spec.reference_curve;
        }
        if (kind === "object_frame") {
          const objectName = Object.keys(layout.objects).find((name) => name !== context.entity) ?? firstKey(layout.objects);
          spec.reference = { kind: "object_frame", object: objectName, frame: "center" };
        }
        if (context.kind === "curve-start" && kind !== "curve") spec.transformation = spec.transformation.filter(([name]) => name !== "ts");
        if (context.kind === "object-position" && kind !== "curve" && spec.transformation.some(([name]) => name === "ts")) spec.reference_curve = firstKey(layout.reference_curves);
        if (context.kind === "object-position" && kind !== "curve" && !spec.transformation.some(([name]) => name === "ts")) delete spec.reference_curve;
      }
      if (action === "reference-curve") spec.reference.curve = element.value;
      if (action === "reference-object") {
        spec.reference.object = element.value;
        const object = layout.objects[element.value];
        const frames = typeFrames(layout.types[object.type]);
        if (!frames.includes(spec.reference.frame)) spec.reference.frame = frames[0];
      }
      if (action === "reference-frame") spec.reference.frame = element.value;
      if (action === "station-curve") spec.reference_curve = element.value;
    }, "Reference updated");
  }

  function handleChange(event) {
    const element = event.target;
    if (element.matches("select[data-select-kind]")) {
      const kind = element.dataset.selectKind;
      state.current[kind] = element.value;
      if (kind === "curve") state.selection = { kind: "curve", name: element.value };
      if (kind === "object") {
        state.selection = { kind: "object", name: element.value };
        const object = state.layout.objects[element.value];
        if (object) state.current.type = object.type;
      }
      renderAll();
      return;
    }
    if (!element.dataset.action) return;
    const action = element.dataset.action;
    if (["reference-kind", "reference-curve", "reference-object", "reference-frame", "station-curve"].includes(action)) return handleReferenceChange(action, element);

    if (action === "operation-name") {
      const context = parseContext(element);
      const index = operationIndex(element);
      commit((layout) => {
        const spec = transformationFor(layout, context);
        const [oldName, rawValue] = spec.transformation[index];
        const displayValue = M.operationDisplayValue(oldName, rawValue);
        const newName = element.value;
        spec.transformation[index] = [newName, M.operationJsonValue(newName, displayValue)];
        if (context.kind === "object-position") {
          const hasTs = spec.transformation.some(([name]) => name === "ts");
          if (spec.reference.kind !== "curve" && hasTs && !spec.reference_curve) spec.reference_curve = firstKey(layout.reference_curves);
          if (!hasTs) delete spec.reference_curve;
        }
      }, "Operation changed");
      return;
    }
    if (action === "operation-value") {
      const context = parseContext(element);
      const index = operationIndex(element);
      try {
        const displayValue = parseFinite(element.value, "Operation value");
        commit((layout) => {
          const operation = transformationFor(layout, context).transformation[index][0];
          transformationFor(layout, context).transformation[index][1] = M.operationJsonValue(operation, displayValue);
        }, "Operation value updated");
      } catch (error) { setStatus("error", errorMessage(error)); }
      return;
    }
    if (action === "segment-value") {
      try {
        const curveName = element.dataset.curveName;
        const index = Number(element.dataset.segmentIndex);
        const field = Number(element.dataset.segmentField);
        let value = parseFinite(element.value, "Segment value");
        if (field === 0 && value <= 0) throw new M.LayoutError("Segment length must be positive");
        if (field > 0) value *= M.DEG;
        commit((layout) => { layout.reference_curves[curveName].segments[index][field] = value; }, "Segment updated");
      } catch (error) { setStatus("error", errorMessage(error)); }
      return;
    }
    if (action === "curve-color" || action === "type-color") {
      const value = element.value.trim();
      const entityName = action === "curve-color" ? element.dataset.curveName : element.dataset.typeName;
      commit((layout) => {
        if (action === "curve-color") layout.reference_curves[entityName].color = value;
        else layout.types[entityName].color = value;
      }, "Color updated");
      return;
    }
    if (action === "type-primitive") {
      const typeName = element.dataset.typeName;
      commit((layout) => {
        const type = layout.types[typeName];
        const shape = M.typeShape(type);
        type.shape = element.value === "box"
          ? ["box", shape.primitive === "box" ? shape.dx : shape.radius * 2, shape.primitive === "box" ? shape.dy : shape.radius * 2, shape.dz, shape.curvature, shape.roll]
          : ["cylinder", shape.primitive === "cylinder" ? shape.radius : Math.max(shape.dx, shape.dy) / 2, shape.dz, shape.curvature, shape.roll];
      }, `Primitive changed to ${element.value}`);
      return;
    }
    if (action === "type-shape-value") {
      try {
        const typeName = element.dataset.typeName;
        const field = element.dataset.shapeField;
        let value = parseFinite(element.value, `Shape ${field}`);
        if (["dx", "dy", "dz", "radius"].includes(field) && value <= 0) throw new M.LayoutError(`${field} must be positive`);
        if (field === "roll") value *= M.DEG;
        commit((layout) => {
          const type = layout.types[typeName];
          const shape = M.typeShape(type);
          shape[field] = value;
          type.shape = shape.primitive === "box"
            ? ["box", shape.dx, shape.dy, shape.dz, shape.curvature, shape.roll]
            : ["cylinder", shape.radius, shape.dz, shape.curvature, shape.roll];
        }, "Shape updated");
      } catch (error) { setStatus("error", errorMessage(error)); }
      return;
    }
    if (action === "magnetic-length") {
      try {
        const value = parseFinite(element.value, "Magnetic length");
        if (value <= 0) throw new M.LayoutError("Magnetic length must be positive");
        const typeName = element.dataset.typeName;
        commit((layout) => { layout.types[typeName].magnetic_length = value; }, "Magnetic length updated");
      } catch (error) { setStatus("error", errorMessage(error)); }
      return;
    }
    if (action === "select-frame") {
      state.frameByType[state.current.type] = element.value;
      const instance = Object.entries(state.layout.objects).find(([, object]) => object.type === state.current.type)?.[0];
      if (instance) state.selection = { kind: "frame", object: instance, name: element.value };
      renderAll();
      return;
    }
    if (action === "object-type") {
      const objectName = state.current.object;
      commit((layout) => {
        const object = layout.objects[objectName];
        object.type = element.value;
        const frames = typeFrames(layout.types[element.value]);
        if (!frames.includes(object.position.target)) object.position.target = "center";
        state.current.type = element.value;
      }, "Object type updated");
      return;
    }
    if (action === "object-target") {
      const objectName = state.current.object;
      commit((layout) => { layout.objects[objectName].position.target = element.value; }, "Target frame updated");
    }
  }

  function handleInput(event) {
    const element = event.target;
    if (!element.matches("[data-filter-kind]")) return;
    const kind = element.dataset.filterKind;
    state.filters[kind] = element.value;
    updatePicker(kind);
  }

  async function loadText(text, label) {
    state.loading = true;
    setStatus("loading", `Loading ${label}…`, 0);
    try {
      const parsed = JSON.parse(text);
      loadLayout(parsed, `${label} loaded`);
    } catch (error) {
      setStatus("error", `Cannot load ${label}: ${errorMessage(error)}`);
    } finally {
      state.loading = false;
    }
  }

  async function importFile(file) {
    if (!file) return;
    try {
      await loadText(await file.text(), file.name);
    } finally {
      dom.fileInput.value = "";
    }
  }

  async function loadUrl() {
    const value = dom.layoutUrl.value.trim();
    if (!value) return;
    state.loading = true;
    setStatus("loading", `Loading ${value}…`, 0);
    try {
      const url = new URL(value, window.location.href);
      if (!new Set(["http:", "https:", "file:"]).has(url.protocol)) throw new Error(`unsupported URL protocol ${url.protocol}`);
      const response = await fetch(url.href, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
      const parsed = await response.json();
      loadLayout(parsed, `Loaded ${url.pathname.split("/").pop() || url.hostname}`);
    } catch (error) {
      setStatus("error", `Cannot load URL: ${errorMessage(error)}`);
    } finally {
      state.loading = false;
    }
  }

  function downloadJson() {
    const blob = new Blob([`${JSON.stringify(state.layout, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "layout-studio.json";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setStatus("success", "Layout JSON downloaded");
  }

  function clearLayout() {
    commit((layout) => {
      layout.reference_curves = {};
      layout.types = {};
      layout.objects = {};
      state.current = { curve: "", type: "", object: "" };
      state.selection = null;
      state.expanded.clear();
    }, "Layout cleared", { fit: true });
  }

  const viewer = new Viewer(dom.canvas, {
    onSelect: (selection) => selectEntity(selection, { reveal: false }),
    onHover: (hover) => {
      if (!hover) {
        dom.viewerTooltip.hidden = true;
        return;
      }
      dom.viewerTooltip.textContent = hover.kind === "object"
        ? `${hover.name} · ${state.layout.objects[hover.name]?.type ?? "object"}`
        : `${hover.name} · reference curve`;
      dom.viewerTooltip.style.left = `${Math.min(hover.x + 13, dom.canvas.clientWidth - 185)}px`;
      dom.viewerTooltip.style.top = `${Math.max(7, hover.y - 4)}px`;
      dom.viewerTooltip.hidden = false;
    },
  });

  document.addEventListener("click", handleClick);
  document.addEventListener("change", handleChange);
  document.addEventListener("input", handleInput);

  dom.addCurveButton.addEventListener("click", () => handleEntityAction("add-curve"));
  dom.addTypeButton.addEventListener("click", () => handleEntityAction("add-type"));
  dom.addObjectButton.addEventListener("click", () => handleEntityAction("add-object"));
  dom.helpButton.addEventListener("click", () => dom.helpDialog.showModal());
  dom.clearButton.addEventListener("click", () => dom.clearDialog.showModal());
  dom.confirmClearButton.addEventListener("click", clearLayout);
  dom.importButton.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => void importFile(dom.fileInput.files?.[0]));
  dom.downloadButton.addEventListener("click", downloadJson);
  dom.loadUrlButton.addEventListener("click", () => void loadUrl());
  dom.layoutUrl.addEventListener("keydown", (event) => { if (event.key === "Enter") void loadUrl(); });
  dom.fitLayoutButton.addEventListener("click", () => viewer.fit());
  dom.showCurves.addEventListener("change", () => viewer.setVisibility({ curves: dom.showCurves.checked }));
  dom.showObjects.addEventListener("change", () => viewer.setVisibility({ objects: dom.showObjects.checked }));
  dom.showBeamFrames.addEventListener("change", () => viewer.setVisibility({ beamFrames: dom.showBeamFrames.checked }));
  dom.expandAllButton.addEventListener("click", () => {
    const graph = M.buildDependencyGraph(state.layout);
    state.expanded = new Set([...graph.nodes.keys()].filter((id) => (graph.children.get(id) ?? []).length));
    renderDependencies();
  });
  dom.collapseAllButton.addEventListener("click", () => { state.expanded.clear(); renderDependencies(); });

  for (const button of document.querySelectorAll("[data-viewer-mode]")) {
    button.addEventListener("click", () => {
      state.viewerMode = button.dataset.viewerMode;
      for (const peer of document.querySelectorAll("[data-viewer-mode]")) peer.classList.toggle("active", peer === button);
      viewer.setMode(state.viewerMode);
    });
  }

  try {
    state.resolver = makeResolver(state.layout);
    renderAll({ geometryChanged: true, fit: true });
  } catch (error) {
    setStatus("error", errorMessage(error));
  }

  const startupUrl = new URLSearchParams(window.location.search).get("layout");
  if (startupUrl) {
    dom.layoutUrl.value = startupUrl;
    void loadUrl();
  }
})();
