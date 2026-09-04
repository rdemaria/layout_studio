/* Layout Studio dependency-free canvas viewer.
 *
 * The renderer uses a lightweight orthographic camera so the generated
 * index.html works from file:// as well as from an HTTP server.  Geometry is
 * resolved by model.js; this module is intentionally concerned only with
 * interaction, projection and drawing.
 */
(() => {
  "use strict";

  const M = globalThis.LayoutStudioModel;
  if (!M) throw new Error("LayoutStudioModel must be loaded before viewer.js");

  const TAU = 2 * Math.PI;

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function distance2(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function distanceToSegment(point, start, end) {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length2 = dx * dx + dy * dy;
    if (length2 < 1e-12) return distance2(point, start);
    const t = clamp(((point.x - start.x) * dx + (point.y - start.y) * dy) / length2, 0, 1);
    return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy));
  }

  function rgba(hex, alpha) {
    const value = /^#[0-9a-f]{6}$/i.test(hex) ? hex.slice(1) : "ffffff";
    const red = Number.parseInt(value.slice(0, 2), 16);
    const green = Number.parseInt(value.slice(2, 4), 16);
    const blue = Number.parseInt(value.slice(4, 6), 16);
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }

  function roundedRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r);
    ctx.closePath();
  }

  function selectionMatches(selection, kind, name) {
    if (!selection) return false;
    if (kind === "curve") return selection.kind === "curve" && selection.name === name;
    return (
      (selection.kind === "object" && selection.name === name) ||
      (selection.kind === "frame" && selection.object === name)
    );
  }

  class LayoutViewer {
    constructor(canvas, options = {}) {
      if (!(canvas instanceof HTMLCanvasElement)) {
        throw new TypeError("LayoutViewer requires a canvas element");
      }
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d", { alpha: false });
      if (!this.ctx) throw new Error("Canvas 2D is not available");

      this.onSelect = options.onSelect ?? (() => {});
      this.onHover = options.onHover ?? (() => {});
      this.onViewChange = options.onViewChange ?? (() => {});
      this.layout = null;
      this.resolver = null;
      this.scene = { curves: [], objects: [], bounds: null };
      this.selection = null;
      this.hover = null;
      this.mode = "orbit";
      this.visibility = { curves: true, objects: true, beamFrames: false };
      this.camera = {
        target: [0, 0, 0],
        yaw: -0.72,
        pitch: 0.48,
        scale: 42,
      };
      this.cssWidth = 1;
      this.cssHeight = 1;
      this.pixelRatio = 1;
      this.basis = null;
      this.hitTargets = [];
      this.pendingDraw = 0;
      this.pointer = null;
      this.dragDistance = 0;
      this.lastPointer = null;
      this.sceneError = null;

      this.boundResize = () => this.resize();
      this.boundPointerDown = (event) => this.pointerDown(event);
      this.boundPointerMove = (event) => this.pointerMove(event);
      this.boundPointerUp = (event) => this.pointerUp(event);
      this.boundPointerLeave = () => this.pointerLeave();
      this.boundWheel = (event) => this.wheel(event);
      this.boundDoubleClick = () => this.fit();

      canvas.addEventListener("pointerdown", this.boundPointerDown);
      canvas.addEventListener("pointermove", this.boundPointerMove);
      canvas.addEventListener("pointerup", this.boundPointerUp);
      canvas.addEventListener("pointercancel", this.boundPointerUp);
      canvas.addEventListener("pointerleave", this.boundPointerLeave);
      canvas.addEventListener("wheel", this.boundWheel, { passive: false });
      canvas.addEventListener("dblclick", this.boundDoubleClick);
      canvas.addEventListener("contextmenu", (event) => event.preventDefault());

      this.resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(this.boundResize) : null;
      this.resizeObserver?.observe(canvas.parentElement ?? canvas);
      window.addEventListener("resize", this.boundResize);
      this.updateCursor();
      this.resize();
    }

    destroy() {
      cancelAnimationFrame(this.pendingDraw);
      this.resizeObserver?.disconnect();
      window.removeEventListener("resize", this.boundResize);
      this.canvas.removeEventListener("pointerdown", this.boundPointerDown);
      this.canvas.removeEventListener("pointermove", this.boundPointerMove);
      this.canvas.removeEventListener("pointerup", this.boundPointerUp);
      this.canvas.removeEventListener("pointercancel", this.boundPointerUp);
      this.canvas.removeEventListener("pointerleave", this.boundPointerLeave);
      this.canvas.removeEventListener("wheel", this.boundWheel);
      this.canvas.removeEventListener("dblclick", this.boundDoubleClick);
    }

    setLayout(layout, resolver = null, { fit = false } = {}) {
      this.layout = layout;
      this.resolver = resolver ?? new M.Resolver(layout).resolveAll();
      this.rebuildScene();
      if (fit) this.fit();
      else this.requestDraw();
    }

    setSelection(selection) {
      this.selection = selection;
      this.requestDraw();
    }

    setMode(mode) {
      if (!new Set(["orbit", "pan", "select"]).has(mode)) return;
      this.mode = mode;
      this.updateCursor();
      this.requestDraw();
    }

    setVisibility(next) {
      Object.assign(this.visibility, next);
      this.requestDraw();
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const width = Math.max(1, Math.round(rect.width));
      const height = Math.max(1, Math.round(rect.height));
      const ratio = clamp(window.devicePixelRatio || 1, 1, 2.5);
      if (width === this.cssWidth && height === this.cssHeight && ratio === this.pixelRatio) return;
      this.cssWidth = width;
      this.cssHeight = height;
      this.pixelRatio = ratio;
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
      this.requestDraw();
    }

    rebuildScene() {
      const curves = [];
      const objects = [];
      this.sceneError = null;
      try {
        for (const [name, curve] of Object.entries(this.layout.reference_curves)) {
          const data = this.resolver.curveData(name);
          curves.push({
            name,
            color: curve.color,
            points: data.samples.map((sample) => sample.frame.o.slice()),
            stations: data.samples.map((sample) => sample.station),
          });
        }
        for (const name of Object.keys(this.layout.objects)) {
          const geometry = this.resolver.objectGeometry(name);
          const center = geometry.center.frame;
          const shape = geometry.shape;
          const startLocal = M.typePathFrame(geometry.type, -shape.dz / 2);
          const endLocal = M.typePathFrame(geometry.type, shape.dz / 2);
          const start = M.composeFrames(center, startLocal);
          const end = M.composeFrames(center, endLocal);
          const magneticEntry = this.resolver.objectFrame(name, "magnetic_entry").frame;
          const magneticExit = this.resolver.objectFrame(name, "magnetic_exit").frame;
          objects.push({
            name,
            typeName: geometry.object.type,
            type: geometry.type,
            shape,
            color: geometry.type.color,
            center,
            start,
            end,
            magneticEntry,
            magneticExit,
          });
        }
        this.scene = {
          curves,
          objects,
          bounds: M.layoutBounds(this.layout, this.resolver),
        };
      } catch (error) {
        this.scene = { curves, objects, bounds: null };
        this.sceneError = error instanceof Error ? error.message : String(error);
      }
    }

    fit() {
      if (!this.scene.bounds) {
        this.camera.target = [0, 0, 0];
        this.camera.scale = 42;
        this.requestDraw();
        return;
      }
      this.camera.target = this.scene.bounds.center.slice();
      this.updateBasis();
      const { min, max } = this.scene.bounds;
      const corners = [];
      for (const x of [min[0], max[0]]) {
        for (const y of [min[1], max[1]]) {
          for (const z of [min[2], max[2]]) corners.push([x, y, z]);
        }
      }
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (const point of corners) {
        const delta = M.sub(point, this.camera.target);
        const x = M.dot(delta, this.basis.right);
        const y = M.dot(delta, this.basis.up);
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      }
      const worldWidth = Math.max(1e-6, maxX - minX);
      const worldHeight = Math.max(1e-6, maxY - minY);
      const padding = Math.min(90, Math.max(36, Math.min(this.cssWidth, this.cssHeight) * 0.11));
      this.camera.scale = clamp(
        Math.min((this.cssWidth - 2 * padding) / worldWidth, (this.cssHeight - 2 * padding) / worldHeight),
        0.002,
        5000,
      );
      this.onViewChange({ ...this.camera });
      this.requestDraw();
    }

    frameSelection(frame, padding = 80) {
      if (!frame) return;
      this.camera.target = frame.o.slice();
      this.camera.scale = Math.max(this.camera.scale, Math.min(this.cssWidth, this.cssHeight) / Math.max(4, padding / 10));
      this.requestDraw();
    }

    updateBasis() {
      const cp = Math.cos(this.camera.pitch);
      const view = M.unit([
        cp * Math.sin(this.camera.yaw),
        Math.sin(this.camera.pitch),
        cp * Math.cos(this.camera.yaw),
      ]);
      let right = M.cross([0, 1, 0], view);
      if (M.norm(right) < 1e-8) right = [1, 0, 0];
      right = M.unit(right);
      const up = M.unit(M.cross(view, right));
      this.basis = { view, right, up };
    }

    project(point) {
      const delta = M.sub(point, this.camera.target);
      return {
        x: this.cssWidth / 2 + M.dot(delta, this.basis.right) * this.camera.scale,
        y: this.cssHeight / 2 - M.dot(delta, this.basis.up) * this.camera.scale,
        depth: M.dot(delta, this.basis.view),
      };
    }

    unprojectScreenDelta(dx, dy) {
      return M.add(
        M.scale(this.basis.right, -dx / this.camera.scale),
        M.scale(this.basis.up, dy / this.camera.scale),
      );
    }

    requestDraw() {
      if (this.pendingDraw) return;
      this.pendingDraw = requestAnimationFrame(() => {
        this.pendingDraw = 0;
        this.draw();
      });
    }

    draw() {
      this.updateBasis();
      const ctx = this.ctx;
      ctx.save();
      ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
      this.drawBackground(ctx);
      this.hitTargets = [];
      this.drawGrid(ctx);
      this.drawAxes(ctx);
      if (this.layout) {
        if (this.visibility.curves) this.drawCurves(ctx);
        if (this.visibility.objects) this.drawObjects(ctx);
      }
      this.drawCompass(ctx);
      if (this.sceneError) this.drawError(ctx, this.sceneError);
      if (!this.layout || (!this.scene.curves.length && !this.scene.objects.length)) this.drawEmpty(ctx);
      ctx.restore();
    }

    drawBackground(ctx) {
      const gradient = ctx.createLinearGradient(0, 0, 0, this.cssHeight);
      gradient.addColorStop(0, "#0a111a");
      gradient.addColorStop(1, "#070b11");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, this.cssWidth, this.cssHeight);
    }

    niceGridStep() {
      const targetPixels = 74;
      const raw = targetPixels / Math.max(this.camera.scale, 1e-9);
      const exponent = 10 ** Math.floor(Math.log10(raw));
      const ratio = raw / exponent;
      const multiplier = ratio < 1.5 ? 1 : ratio < 3.5 ? 2 : ratio < 7.5 ? 5 : 10;
      return exponent * multiplier;
    }

    drawGrid(ctx) {
      const step = this.niceGridStep();
      const extent = Math.max(8, Math.ceil(Math.max(this.cssWidth, this.cssHeight) / this.camera.scale / step) + 3);
      const centerX = Math.round(this.camera.target[0] / step);
      const centerZ = Math.round(this.camera.target[2] / step);
      ctx.save();
      ctx.lineWidth = 1;
      for (let i = -extent; i <= extent; i += 1) {
        const x = (centerX + i) * step;
        const a = this.project([x, 0, (centerZ - extent) * step]);
        const b = this.project([x, 0, (centerZ + extent) * step]);
        ctx.strokeStyle = i === -centerX ? "rgba(112, 139, 155, .24)" : "rgba(106, 130, 145, .09)";
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      for (let i = -extent; i <= extent; i += 1) {
        const z = (centerZ + i) * step;
        const a = this.project([(centerX - extent) * step, 0, z]);
        const b = this.project([(centerX + extent) * step, 0, z]);
        ctx.strokeStyle = i === -centerZ ? "rgba(112, 139, 155, .24)" : "rgba(106, 130, 145, .09)";
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      ctx.restore();
    }

    drawAxes(ctx) {
      const boundsSize = this.scene.bounds ? Math.max(...this.scene.bounds.size, 1) : 5;
      const length = Math.max(1, Math.min(boundsSize * 0.16, 10 / Math.max(this.camera.scale / 25, 0.2)));
      const origin = this.project([0, 0, 0]);
      const axes = [
        { point: [length, 0, 0], color: "#e36b62", label: "X" },
        { point: [0, length, 0], color: "#6dcb8b", label: "Y" },
        { point: [0, 0, length], color: "#6da9e8", label: "Z" },
      ];
      ctx.save();
      ctx.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
      for (const axis of axes) {
        const end = this.project(axis.point);
        ctx.strokeStyle = axis.color;
        ctx.fillStyle = axis.color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
        ctx.fillText(axis.label, end.x + 4, end.y - 3);
      }
      ctx.restore();
    }

    drawCurves(ctx) {
      for (const curve of this.scene.curves) {
        const selected = selectionMatches(this.selection, "curve", curve.name);
        const projected = curve.points.map((point) => this.project(point));
        if (projected.length < 2) continue;
        ctx.save();
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        if (selected) {
          ctx.strokeStyle = rgba(curve.color, 0.22);
          ctx.lineWidth = 10;
          ctx.beginPath();
          ctx.moveTo(projected[0].x, projected[0].y);
          for (let i = 1; i < projected.length; i += 1) ctx.lineTo(projected[i].x, projected[i].y);
          ctx.stroke();
        }
        ctx.strokeStyle = curve.color;
        ctx.lineWidth = selected ? 4.5 : 2.4;
        ctx.beginPath();
        ctx.moveTo(projected[0].x, projected[0].y);
        for (let i = 1; i < projected.length; i += 1) ctx.lineTo(projected[i].x, projected[i].y);
        ctx.stroke();
        ctx.restore();
        for (let i = 1; i < projected.length; i += 1) {
          const a = projected[i - 1];
          const b = projected[i];
          if (Math.max(a.x, b.x) < -12 || Math.min(a.x, b.x) > this.cssWidth + 12 || Math.max(a.y, b.y) < -12 || Math.min(a.y, b.y) > this.cssHeight + 12) continue;
          this.hitTargets.push({
            kind: "curve",
            name: curve.name,
            a,
            b,
            radius: selected ? 9 : 7,
            priority: selected ? 4 : 1,
          });
        }
      }
    }

    drawObjects(ctx) {
      const prepared = [];
      for (const object of this.scene.objects) {
        const center = this.project(object.center.o);
        const start = this.project(object.start.o);
        const end = this.project(object.end.o);
        const radiusWorld = object.shape.primitive === "box" ? Math.hypot(object.shape.dx, object.shape.dy) / 2 : object.shape.radius;
        const size = Math.max(distance2(start, end), radiusWorld * this.camera.scale * 2);
        if (center.x < -Math.max(30, size) || center.x > this.cssWidth + Math.max(30, size) || center.y < -Math.max(30, size) || center.y > this.cssHeight + Math.max(30, size)) continue;
        prepared.push({ object, center, start, end, size, depth: center.depth });
      }
      prepared.sort((a, b) => b.depth - a.depth);
      const simplifiedScene = this.scene.objects.length > 3500;
      for (const item of prepared) {
        const selected = selectionMatches(this.selection, "object", item.object.name);
        const hovered = this.hover?.kind === "object" && this.hover.name === item.object.name;
        if (!simplifiedScene && (item.size > 6 || selected || hovered)) {
          this.drawObjectWireframe(ctx, item.object, selected, hovered);
        } else {
          this.drawObjectGlyph(ctx, item, selected, hovered);
        }
        if (this.visibility.beamFrames && (!simplifiedScene || selected || hovered)) {
          const axisLength = clamp(13 / this.camera.scale, 0.05, 0.7);
          this.drawFrame(ctx, item.object.magneticEntry, axisLength, 0.7);
          this.drawFrame(ctx, item.object.magneticExit, axisLength, 0.7);
        }
        this.hitTargets.push({
          kind: "object",
          name: item.object.name,
          a: item.start,
          b: item.end,
          center: item.center,
          radius: clamp(item.size * 0.35, 5, 14),
          priority: selected ? 5 : 2,
        });
      }
    }

    drawObjectGlyph(ctx, item, selected, hovered) {
      const { object, start, end, center, size } = item;
      ctx.save();
      ctx.lineCap = "round";
      if (selected || hovered) {
        ctx.strokeStyle = rgba(object.color, selected ? 0.34 : 0.2);
        ctx.lineWidth = selected ? 9 : 7;
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }
      ctx.strokeStyle = object.color;
      ctx.lineWidth = selected ? 4 : clamp(size * 0.26, 1.2, 3);
      ctx.beginPath();
      ctx.moveTo(start.x, start.y);
      ctx.lineTo(end.x, end.y);
      ctx.stroke();
      if (distance2(start, end) < 2.5) {
        ctx.fillStyle = object.color;
        ctx.beginPath();
        ctx.arc(center.x, center.y, selected ? 4.5 : 2.5, 0, TAU);
        ctx.fill();
      }
      ctx.restore();
    }

    crossSection(frame, shape, phase = 0) {
      if (shape.primitive === "box") {
        const hx = shape.dx / 2;
        const hy = shape.dy / 2;
        return [
          M.add(frame.o, M.add(M.scale(frame.x, -hx), M.scale(frame.y, -hy))),
          M.add(frame.o, M.add(M.scale(frame.x, hx), M.scale(frame.y, -hy))),
          M.add(frame.o, M.add(M.scale(frame.x, hx), M.scale(frame.y, hy))),
          M.add(frame.o, M.add(M.scale(frame.x, -hx), M.scale(frame.y, hy))),
        ];
      }
      const points = [];
      const count = 12;
      for (let i = 0; i < count; i += 1) {
        const angle = phase + (TAU * i) / count;
        points.push(
          M.add(
            frame.o,
            M.add(M.scale(frame.x, shape.radius * Math.cos(angle)), M.scale(frame.y, shape.radius * Math.sin(angle))),
          ),
        );
      }
      return points;
    }

    drawObjectWireframe(ctx, object, selected, hovered) {
      const start = this.crossSection(object.start, object.shape);
      const end = this.crossSection(object.end, object.shape);
      const ps = start.map((point) => this.project(point));
      const pe = end.map((point) => this.project(point));
      const count = ps.length;
      const drawPath = (points) => {
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i].x, points[i].y);
        ctx.closePath();
        ctx.stroke();
      };
      ctx.save();
      if (selected || hovered) {
        ctx.strokeStyle = rgba(object.color, selected ? 0.34 : 0.22);
        ctx.lineWidth = selected ? 6.5 : 5;
        drawPath(ps);
        drawPath(pe);
        for (let i = 0; i < count; i += 1) {
          ctx.beginPath();
          ctx.moveTo(ps[i].x, ps[i].y);
          ctx.lineTo(pe[i].x, pe[i].y);
          ctx.stroke();
        }
      }
      ctx.strokeStyle = selected ? "#f6feff" : object.color;
      ctx.lineWidth = selected ? 1.8 : 1.15;
      ctx.globalAlpha = selected ? 0.98 : 0.82;
      drawPath(ps);
      drawPath(pe);
      for (let i = 0; i < count; i += 1) {
        ctx.beginPath();
        ctx.moveTo(ps[i].x, ps[i].y);
        ctx.lineTo(pe[i].x, pe[i].y);
        ctx.stroke();
      }
      const center = this.project(object.center.o);
      ctx.fillStyle = object.color;
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.arc(center.x, center.y, selected ? 3.2 : 2, 0, TAU);
      ctx.fill();
      ctx.restore();
    }

    drawFrame(ctx, frame, length, alpha = 1) {
      const origin = this.project(frame.o);
      const axes = [
        { vector: frame.x, color: `rgba(227, 107, 98, ${alpha})` },
        { vector: frame.y, color: `rgba(109, 203, 139, ${alpha})` },
        { vector: frame.s, color: `rgba(109, 169, 232, ${alpha})` },
      ];
      ctx.save();
      ctx.lineWidth = 1.25;
      for (const axis of axes) {
        const end = this.project(M.add(frame.o, M.scale(axis.vector, length)));
        ctx.strokeStyle = axis.color;
        ctx.beginPath();
        ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }
      ctx.restore();
    }

    drawCompass(ctx) {
      const x = this.cssWidth - 47;
      const y = this.cssHeight - 45;
      const radius = 24;
      ctx.save();
      ctx.fillStyle = "rgba(9, 17, 26, .78)";
      ctx.strokeStyle = "rgba(116, 144, 160, .32)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, TAU);
      ctx.fill();
      ctx.stroke();
      const axes = [
        { vector: [1, 0, 0], color: "#e36b62", label: "x" },
        { vector: [0, 1, 0], color: "#6dcb8b", label: "y" },
        { vector: [0, 0, 1], color: "#6da9e8", label: "s" },
      ];
      ctx.font = "600 9px ui-monospace, SFMono-Regular, Consolas, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const axis of axes) {
        const sx = M.dot(axis.vector, this.basis.right);
        const sy = -M.dot(axis.vector, this.basis.up);
        const length = 15;
        ctx.strokeStyle = axis.color;
        ctx.fillStyle = axis.color;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + sx * length, y + sy * length);
        ctx.stroke();
        ctx.fillText(axis.label, x + sx * (length + 7), y + sy * (length + 7));
      }
      ctx.restore();
    }

    drawError(ctx, message) {
      const width = Math.min(this.cssWidth - 40, 520);
      const x = (this.cssWidth - width) / 2;
      const y = 30;
      ctx.save();
      roundedRect(ctx, x, y, width, 58, 8);
      ctx.fillStyle = "rgba(70, 25, 25, .94)";
      ctx.fill();
      ctx.strokeStyle = "rgba(222, 107, 97, .72)";
      ctx.stroke();
      ctx.fillStyle = "#ffd6d2";
      ctx.font = "600 12px system-ui, sans-serif";
      ctx.fillText("Geometry could not be resolved", x + 14, y + 21);
      ctx.fillStyle = "#e9aaa4";
      ctx.font = "11px ui-monospace, SFMono-Regular, Consolas, monospace";
      const text = message.length > 76 ? `${message.slice(0, 75)}…` : message;
      ctx.fillText(text, x + 14, y + 42);
      ctx.restore();
    }

    drawEmpty(ctx) {
      ctx.save();
      ctx.textAlign = "center";
      ctx.fillStyle = "#7d919e";
      ctx.font = "600 14px system-ui, sans-serif";
      ctx.fillText("No geometry to display", this.cssWidth / 2, this.cssHeight / 2 - 6);
      ctx.fillStyle = "#607681";
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText("Add a curve or a positioned object.", this.cssWidth / 2, this.cssHeight / 2 + 17);
      ctx.restore();
    }

    localPointer(event) {
      const rect = this.canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    pointerDown(event) {
      if (event.button !== 0 && event.button !== 1 && event.button !== 2) return;
      const point = this.localPointer(event);
      this.canvas.setPointerCapture(event.pointerId);
      this.pointer = {
        id: event.pointerId,
        start: point,
        last: point,
        button: event.button,
        action: event.button === 2 || event.shiftKey ? "pan" : this.mode === "select" ? "select" : this.mode,
      };
      this.dragDistance = 0;
      this.updateCursor(true);
      event.preventDefault();
    }

    pointerMove(event) {
      const point = this.localPointer(event);
      this.lastPointer = point;
      if (this.pointer && this.pointer.id === event.pointerId) {
        const dx = point.x - this.pointer.last.x;
        const dy = point.y - this.pointer.last.y;
        this.dragDistance += Math.hypot(dx, dy);
        if (this.pointer.action === "orbit") {
          this.camera.yaw -= dx * 0.009;
          this.camera.pitch = clamp(this.camera.pitch + dy * 0.009, -Math.PI / 2 + 0.025, Math.PI / 2 - 0.025);
          this.onViewChange({ ...this.camera });
          this.requestDraw();
        } else if (this.pointer.action === "pan") {
          this.updateBasis();
          this.camera.target = M.add(this.camera.target, this.unprojectScreenDelta(dx, dy));
          this.onViewChange({ ...this.camera });
          this.requestDraw();
        }
        this.pointer.last = point;
        event.preventDefault();
        return;
      }
      this.updateHover(point);
    }

    pointerUp(event) {
      if (!this.pointer || this.pointer.id !== event.pointerId) return;
      const point = this.localPointer(event);
      const action = this.pointer.action;
      const wasClick = this.dragDistance < 4;
      this.pointer = null;
      try {
        this.canvas.releasePointerCapture(event.pointerId);
      } catch {
        // The capture may already have been released by the browser.
      }
      this.updateCursor(false);
      if (wasClick && (action === "select" || this.mode === "select" || event.button === 0)) {
        const hit = this.pick(point);
        if (!hit) this.onSelect(null);
        else {
          const selection = { kind: hit.kind, name: hit.name };
          if (selectionMatches(this.selection, hit.kind, hit.name)) this.onSelect(null);
          else this.onSelect(selection);
        }
      }
      this.updateHover(point);
    }

    pointerLeave() {
      if (!this.pointer && this.hover) {
        this.hover = null;
        this.onHover(null);
        this.requestDraw();
      }
    }

    wheel(event) {
      event.preventDefault();
      const point = this.localPointer(event);
      this.updateBasis();
      const before = this.unprojectScreenDelta(point.x - this.cssWidth / 2, point.y - this.cssHeight / 2);
      const factor = Math.exp(-event.deltaY * 0.00125);
      this.camera.scale = clamp(this.camera.scale * factor, 0.001, 100000);
      const after = this.unprojectScreenDelta(point.x - this.cssWidth / 2, point.y - this.cssHeight / 2);
      this.camera.target = M.add(this.camera.target, M.sub(before, after));
      this.onViewChange({ ...this.camera });
      this.requestDraw();
    }

    pick(point) {
      let best = null;
      for (const target of this.hitTargets) {
        let distance;
        if (target.a && target.b) distance = distanceToSegment(point, target.a, target.b);
        else if (target.center) distance = distance2(point, target.center);
        else continue;
        if (distance > target.radius) continue;
        const score = distance - (target.priority ?? 0) * 0.35;
        if (!best || score < best.score) best = { ...target, score, distance };
      }
      return best;
    }

    updateHover(point) {
      const hit = this.pick(point);
      const next = hit ? { kind: hit.kind, name: hit.name } : null;
      const changed = next?.kind !== this.hover?.kind || next?.name !== this.hover?.name;
      if (!changed) return;
      this.hover = next;
      this.onHover(next ? { ...next, x: point.x, y: point.y } : null);
      this.requestDraw();
    }

    updateCursor(dragging = false) {
      if (dragging) {
        this.canvas.style.cursor = this.pointer?.action === "pan" ? "grabbing" : this.pointer?.action === "orbit" ? "grabbing" : "crosshair";
      } else if (this.mode === "pan") {
        this.canvas.style.cursor = "grab";
      } else if (this.mode === "select") {
        this.canvas.style.cursor = "crosshair";
      } else {
        this.canvas.style.cursor = "grab";
      }
    }
  }

  globalThis.LayoutStudioViewer = Object.freeze({ LayoutViewer });
})();
