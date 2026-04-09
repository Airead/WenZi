/**
 * annotation.js — Annotation canvas controller for WenZi screenshot tool.
 *
 * Communicates with Python via the `wz` bridge (injected by WKWebView):
 *   wz.send(event, data)  — fire event to Python
 *   wz.on(event, callback) — listen for events from Python
 *
 * Initialization: Python sends "init" event with {imageUrl, width, height}.
 * Export: Python sends "export" event; JS responds with "exported" + dataUrl.
 */

(function () {
  "use strict";

  // ── State ──

  var canvas = null;
  var bgImage = null; // background fabric.Image (not selectable)
  var hiddenCanvas = null; // for mosaic pixel reading
  var hiddenCtx = null;

  var currentTool = null; // null | "rect" | "ellipse" | "arrow" | "line" | "pen" | "mosaic" | "text" | "number"
  var currentColor = "#ff4d4f";
  var currentThickness = "medium";
  var numberCounter = 1;

  // Thickness values in pixels
  var THICKNESS = { thin: 2, medium: 4, thick: 6 };
  // Mosaic block sizes per thickness
  var MOSAIC_BLOCK = { thin: 8, medium: 12, thick: 18 };

  // Undo/Redo
  var stateStack = [];
  var stateIndex = -1;
  var MAX_STACK = 50;
  var savingState = false; // guard against recursive saves

  // Drag state for shape tools
  var isDrawing = false;
  var drawStart = null; // {x, y}
  var activeShape = null; // the shape being drawn

  // Mosaic brush state
  var mosaicState = null; // { tmpCanvas, tmpCtx, mask, bounds, img, lastPoint }

  // ── Initialization ──

  // Signal Python that handlers are registered
  wz.send("ready");

  wz.on("init", function (data) {
    var w = data.width;
    var h = data.height;
    var windowWidth = data.windowWidth || w;
    var imageUrl = data.imageUrl;

    // Center canvas when window is wider than the image
    if (windowWidth > w) {
      var offset = Math.floor((windowWidth - w) / 2);
      var container = document.getElementById("annotation-canvas").parentElement || document.body;
      var wrapper = document.getElementById("canvas-wrapper");
      if (!wrapper) {
        wrapper = document.createElement("div");
        wrapper.id = "canvas-wrapper";
        wrapper.style.position = "absolute";
        wrapper.style.top = "0";
        wrapper.style.left = offset + "px";
        wrapper.style.width = w + "px";
        wrapper.style.height = h + "px";
        var canvasEl = document.getElementById("annotation-canvas");
        canvasEl.parentNode.insertBefore(wrapper, canvasEl);
        wrapper.appendChild(canvasEl);
      }
    }

    // Create Fabric canvas
    canvas = new fabric.Canvas("annotation-canvas", {
      width: w,
      height: h,
      selection: false,
      renderOnAddRemove: true,
    });

    // Load background image
    fabric.FabricImage.fromURL(imageUrl).then(
      function (img) {
        bgImage = img;
        img.set({
          left: 0,
          top: 0,
          scaleX: w / img.width,
          scaleY: h / img.height,
          selectable: false,
          evented: false,
          excludeFromExport: false,
        });
        canvas.backgroundImage = img;
        canvas.renderAll();

        // Create hidden canvas for mosaic pixel reading
        hiddenCanvas = document.createElement("canvas");
        hiddenCanvas.width = w;
        hiddenCanvas.height = h;
        hiddenCtx = hiddenCanvas.getContext("2d", {
          willReadFrequently: true,
        });
        // Draw background onto hidden canvas
        hiddenCtx.drawImage(img.getElement(), 0, 0, w, h);

        // Save initial state
        saveState();
        updateUndoRedoButtons();
      }
    );

    // Wire up canvas events
    setupCanvasEvents();
  });

  // ── Export ──

  wz.on("export", function () {
    if (!canvas) return;
    var dataUrl = canvas.toDataURL({ format: "png", multiplier: 1 });
    wz.send("exported", { dataUrl: dataUrl });
  });

  // ── Canvas Events ──

  function setupCanvasEvents() {
    canvas.on("mouse:down", onMouseDown);
    canvas.on("mouse:move", onMouseMove);
    canvas.on("mouse:up", onMouseUp);

    // Double-click on canvas = confirm
    canvas.on("mouse:dblclick", function () {
      // Only confirm if not currently editing text
      var activeObj = canvas.getActiveObject();
      if (activeObj && activeObj.isEditing) return;
      wz.send("confirm");
    });

    // Save state when objects are modified (moved, scaled, rotated)
    canvas.on("object:modified", function () {
      saveState();
    });

    // Save state after free drawing path is created
    canvas.on("path:created", function () {
      saveState();
    });
  }

  function onMouseDown(opt) {
    if (!currentTool) return;
    var pointer = canvas.getViewportPoint(opt.e);

    if (currentTool === "text") {
      placeText(pointer);
      return;
    }

    if (currentTool === "number") {
      placeNumber(pointer);
      return;
    }

    if (currentTool === "pen") {
      // PencilBrush handles its own drawing
      return;
    }

    // Shape tools: rect, ellipse, arrow, line, mosaic
    isDrawing = true;
    drawStart = { x: pointer.x, y: pointer.y };
    canvas.selection = false;

    if (currentTool === "rect") {
      activeShape = new fabric.Rect({
        left: pointer.x,
        top: pointer.y,
        width: 0,
        height: 0,
        fill: "transparent",
        stroke: currentColor,
        strokeWidth: getThickness(),
        strokeUniform: true,
        selectable: false,
        evented: false,
      });
      canvas.add(activeShape);
    } else if (currentTool === "ellipse") {
      activeShape = new fabric.Ellipse({
        left: pointer.x,
        top: pointer.y,
        rx: 0,
        ry: 0,
        fill: "transparent",
        stroke: currentColor,
        strokeWidth: getThickness(),
        strokeUniform: true,
        selectable: false,
        evented: false,
      });
      canvas.add(activeShape);
    } else if (currentTool === "arrow" || currentTool === "line") {
      activeShape = new fabric.Line(
        [pointer.x, pointer.y, pointer.x, pointer.y],
        {
          stroke: currentColor,
          strokeWidth: getThickness(),
          selectable: false,
          evented: false,
        }
      );
      canvas.add(activeShape);
    } else if (currentTool === "mosaic") {
      activeShape = null;
      mosaicBeginStroke(pointer);
    }
  }

  function onMouseMove(opt) {
    if (!isDrawing || !drawStart) return;
    var pointer = canvas.getViewportPoint(opt.e);

    if (currentTool === "rect" && activeShape) {
      var left = Math.min(drawStart.x, pointer.x);
      var top = Math.min(drawStart.y, pointer.y);
      var width = Math.abs(pointer.x - drawStart.x);
      var height = Math.abs(pointer.y - drawStart.y);
      activeShape.set({ left: left, top: top, width: width, height: height });
      canvas.renderAll();
    } else if (currentTool === "ellipse" && activeShape) {
      var left2 = Math.min(drawStart.x, pointer.x);
      var top2 = Math.min(drawStart.y, pointer.y);
      var rx = Math.abs(pointer.x - drawStart.x) / 2;
      var ry = Math.abs(pointer.y - drawStart.y) / 2;
      activeShape.set({ left: left2, top: top2, rx: rx, ry: ry });
      canvas.renderAll();
    } else if (
      (currentTool === "arrow" || currentTool === "line") &&
      activeShape
    ) {
      activeShape.set({ x2: pointer.x, y2: pointer.y });
      canvas.renderAll();
    } else if (currentTool === "mosaic" && mosaicState) {
      mosaicAddPoint(pointer);
    }
  }

  function onMouseUp(opt) {
    if (!isDrawing) return;
    isDrawing = false;

    var pointer = canvas.getViewportPoint(opt.e);

    if (currentTool === "arrow" && activeShape) {
      // Replace the plain line with line + arrowhead group
      var x1 = activeShape.x1;
      var y1 = activeShape.y1;
      var x2 = activeShape.x2;
      var y2 = activeShape.y2;
      canvas.remove(activeShape);

      if (Math.abs(x2 - x1) > 2 || Math.abs(y2 - y1) > 2) {
        createArrow(x1, y1, x2, y2);
      }
    } else if (currentTool === "mosaic") {
      mosaicEndStroke();
    } else if (activeShape) {
      // For rect, ellipse, line — make them selectable now
      activeShape.set({ selectable: true, evented: true });
      canvas.setActiveObject(activeShape);
    }

    activeShape = null;
    drawStart = null;
    saveState();
  }

  // ── Arrow Tool (WeChat-style filled wedge) ──

  function createArrow(x1, y1, x2, y2) {
    var sw = getThickness();
    var len = Math.sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1));
    if (len < 2) return;

    var angle = Math.atan2(y2 - y1, x2 - x1);
    var cos = Math.cos(angle);
    var sin = Math.sin(angle);

    // Dimensions: sharp tail → gradual taper → moderate head
    var tailW = sw * 0.15;
    var bodyW = sw * 1.5;
    var headW = sw * 4;
    var headLen = Math.min(len * 0.45, sw * 9);
    var bodyLen = len - headLen;

    // Polygon points in local coords (arrow along +X axis)
    // Shape: near-point tail → widens through body → notch → wide head → tip
    var pts = [
      [0, 0],                       // tail point (sharp)
      [bodyLen, -bodyW],            // body top
      [bodyLen, -headW],            // head notch top
      [len, 0],                     // tip
      [bodyLen, headW],             // head notch bottom
      [bodyLen, bodyW],             // body bottom
    ];

    var worldPts = [];
    for (var i = 0; i < pts.length; i++) {
      var lx = pts[i][0], ly = pts[i][1];
      worldPts.push({
        x: x1 + lx * cos - ly * sin,
        y: y1 + lx * sin + ly * cos,
      });
    }

    var arrow = new fabric.Polygon(worldPts, {
      fill: currentColor,
      selectable: true,
      evented: true,
    });
    canvas.add(arrow);
    canvas.setActiveObject(arrow);
  }

  // ── Mosaic Tool (real-time brush with selected color) ──

  function mosaicBeginStroke(pointer) {
    if (!canvas) return;
    var blockSize = MOSAIC_BLOCK[currentThickness] || 12;
    var brushRadius = getThickness() * 6;

    // Full-canvas temp canvas for the mosaic overlay
    var tmp = document.createElement("canvas");
    tmp.width = canvas.width;
    tmp.height = canvas.height;
    var ctx = tmp.getContext("2d");
    ctx.clearRect(0, 0, tmp.width, tmp.height);

    // Track which blocks have been painted (avoid overdraw)
    var painted = {};

    // Live fabric image on canvas
    var img = new fabric.FabricImage(tmp, {
      left: 0, top: 0,
      selectable: false, evented: false,
    });
    canvas.add(img);

    mosaicState = {
      tmpCanvas: tmp, tmpCtx: ctx,
      painted: painted, img: img,
      blockSize: blockSize, brushRadius: brushRadius,
      color: currentColor,
    };

    // Paint first point
    mosaicPaintAt(pointer.x, pointer.y);
    canvas.renderAll();
  }

  function mosaicAddPoint(pointer) {
    if (!mosaicState) return;
    var last = mosaicState.lastPoint;
    if (last) {
      // Interpolate between last and current to avoid gaps
      var dx = pointer.x - last.x, dy = pointer.y - last.y;
      var dist = Math.sqrt(dx * dx + dy * dy);
      var step = mosaicState.blockSize * 0.5;
      if (dist > step) {
        var steps = Math.ceil(dist / step);
        for (var i = 1; i <= steps; i++) {
          var t = i / steps;
          mosaicPaintAt(last.x + dx * t, last.y + dy * t);
        }
      } else {
        mosaicPaintAt(pointer.x, pointer.y);
      }
    } else {
      mosaicPaintAt(pointer.x, pointer.y);
    }
    mosaicState.lastPoint = { x: pointer.x, y: pointer.y };

    // Update the live image
    mosaicState.img.setElement(mosaicState.tmpCanvas);
    canvas.renderAll();
  }

  function mosaicPaintAt(px, py) {
    if (!mosaicState) return;
    var s = mosaicState;
    var bs = s.blockSize, br = s.brushRadius;
    var ctx = s.tmpCtx;

    // Find which blocks this brush circle covers
    var startBx = Math.floor((px - br) / bs);
    var endBx = Math.floor((px + br) / bs);
    var startBy = Math.floor((py - br) / bs);
    var endBy = Math.floor((py + br) / bs);
    var r2 = br * br;

    ctx.fillStyle = s.color;

    for (var by = startBy; by <= endBy; by++) {
      for (var bx = startBx; bx <= endBx; bx++) {
        // Block center
        var cx = bx * bs + bs / 2;
        var cy = by * bs + bs / 2;
        var ddx = cx - px, ddy = cy - py;
        if (ddx * ddx + ddy * ddy > r2) continue;

        var key = bx + "," + by;
        if (s.painted[key]) continue;
        s.painted[key] = true;

        // Clamp to canvas bounds
        var rx = Math.max(0, bx * bs);
        var ry = Math.max(0, by * bs);
        var rw = Math.min(bs, canvas.width - rx);
        var rh = Math.min(bs, canvas.height - ry);
        if (rw > 0 && rh > 0) {
          ctx.fillRect(rx, ry, rw, rh);
        }
      }
    }
  }

  function mosaicEndStroke() {
    if (!mosaicState) return;
    // Keep non-selectable while mosaic tool is active — deactivateTool
    // will re-enable selection on all objects when user switches tools.
    mosaicState = null;
  }

  // ── Text Tool ──

  function placeText(pointer) {
    var text = new fabric.IText("", {
      left: pointer.x,
      top: pointer.y,
      fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
      fontSize: 16 + getThickness() * 2,
      fill: currentColor,
      selectable: true,
      evented: true,
    });
    canvas.add(text);
    canvas.setActiveObject(text);
    text.enterEditing();

    // Save state when editing exits
    text.on("editing:exited", function () {
      // Remove empty text objects
      if (!text.text || text.text.trim() === "") {
        canvas.remove(text);
      }
      saveState();
    });
  }

  // ── Number Marker Tool ──

  function placeNumber(pointer) {
    var radius = 12;
    var circle = new fabric.Circle({
      radius: radius,
      fill: currentColor,
      originX: "center",
      originY: "center",
      selectable: false,
      evented: false,
    });

    var label = new fabric.Text(String(numberCounter), {
      fontSize: 14,
      fill: "#ffffff",
      fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
      fontWeight: "bold",
      originX: "center",
      originY: "center",
      selectable: false,
      evented: false,
    });

    var group = new fabric.Group([circle, label], {
      left: pointer.x,
      top: pointer.y,
      originX: "center",
      originY: "center",
      selectable: true,
      evented: true,
    });

    canvas.add(group);
    canvas.setActiveObject(group);
    numberCounter++;
    saveState();
  }

  // ── Undo / Redo ──

  function saveState() {
    if (savingState || !canvas) return;
    savingState = true;

    // Save only objects, not the background image (which never changes
    // and would bloat each state entry by several MB on Retina screens).
    var objects = canvas.toJSON().objects || [];

    // Trim any redo states ahead of current index
    if (stateIndex < stateStack.length - 1) {
      stateStack = stateStack.slice(0, stateIndex + 1);
    }

    stateStack.push(objects);

    // Enforce max stack depth
    if (stateStack.length > MAX_STACK) {
      stateStack.shift();
    }

    stateIndex = stateStack.length - 1;
    savingState = false;
    updateUndoRedoButtons();
  }

  function undo() {
    if (stateIndex <= 0 || !canvas) return;
    stateIndex--;
    restoreState(stateStack[stateIndex]);
  }

  function redo() {
    if (stateIndex >= stateStack.length - 1 || !canvas) return;
    stateIndex++;
    restoreState(stateStack[stateIndex]);
  }

  function restoreState(objects) {
    if (!canvas) return;
    savingState = true;

    // Clear all objects but preserve the background image
    canvas.remove.apply(canvas, canvas.getObjects());
    fabric.util.enlivenObjects(objects).then(function (enlivened) {
      for (var i = 0; i < enlivened.length; i++) {
        canvas.add(enlivened[i]);
      }
      canvas.renderAll();
      savingState = false;
      updateUndoRedoButtons();
    });
  }

  function updateUndoRedoButtons() {
    var undoBtn = document.getElementById("btn-undo");
    var redoBtn = document.getElementById("btn-redo");
    if (undoBtn) undoBtn.disabled = stateIndex <= 0;
    if (redoBtn) redoBtn.disabled = stateIndex >= stateStack.length - 1;
  }

  // ── Tool Activation ──

  function activateTool(tool) {
    // Deactivate previous tool
    deactivateCurrentTool();

    if (currentTool === tool) {
      // Toggle off — clicking the same tool deselects it
      currentTool = null;
      updateToolbarUI();
      return;
    }

    currentTool = tool;
    updateToolbarUI();

    if (tool === "pen") {
      canvas.isDrawingMode = true;
      canvas.freeDrawingBrush = new fabric.PencilBrush(canvas);
      canvas.freeDrawingBrush.color = currentColor;
      canvas.freeDrawingBrush.width = getThickness();
    } else {
      canvas.isDrawingMode = false;
    }

    if (tool === "text") {
      document
        .querySelector(".canvas-container")
        .classList.add("text-cursor");
    }

    // Disable object selection while a tool is active
    canvas.forEachObject(function (obj) {
      obj.selectable = false;
      obj.evented = false;
    });
    canvas.discardActiveObject();
    canvas.renderAll();
  }

  function deactivateCurrentTool() {
    if (!canvas) return;

    canvas.isDrawingMode = false;
    isDrawing = false;
    activeShape = null;
    drawStart = null;

    var cc = document.querySelector(".canvas-container");
    if (cc) cc.classList.remove("text-cursor");

    // Re-enable object selection
    if (canvas) {
      canvas.forEachObject(function (obj) {
        if (obj !== bgImage) {
          obj.selectable = true;
          obj.evented = true;
        }
      });
    }
  }

  function updateToolbarUI() {
    // Update active state on tool buttons
    var buttons = document.querySelectorAll(".tool-btn[data-tool]");
    for (var i = 0; i < buttons.length; i++) {
      if (buttons[i].getAttribute("data-tool") === currentTool) {
        buttons[i].classList.add("active");
      } else {
        buttons[i].classList.remove("active");
      }
    }

    // Show/hide secondary panel
    var panel = document.getElementById("secondary-panel");
    if (currentTool && currentTool !== "number") {
      // Number tool doesn't need thickness
      panel.classList.remove("hidden");
    } else if (currentTool === "number") {
      // Number still shows color, hide thickness? Keep both for simplicity.
      panel.classList.remove("hidden");
    } else {
      panel.classList.add("hidden");
    }
  }

  // ── Helpers ──

  function getThickness() {
    return THICKNESS[currentThickness] || 4;
  }

  // ── Toolbar Event Delegation ──

  // Tool buttons
  document.getElementById("toolbar").addEventListener("click", function (e) {
    var btn = e.target.closest(".tool-btn");
    if (!btn) return;

    var tool = btn.getAttribute("data-tool");
    if (tool) {
      activateTool(tool);
      return;
    }

    // Undo / Redo
    if (btn.id === "btn-undo") {
      undo();
      return;
    }
    if (btn.id === "btn-redo") {
      redo();
      return;
    }

    // Actions
    if (btn.id === "btn-save") {
      wz.send("save");
      return;
    }
    if (btn.id === "btn-cancel") {
      wz.send("cancel");
      return;
    }
    if (btn.id === "btn-confirm") {
      wz.send("confirm");
      return;
    }
  });

  // Color dots
  document
    .getElementById("color-section")
    .addEventListener("click", function (e) {
      var dot = e.target.closest(".color-dot");
      if (!dot) return;

      // Update selection
      var dots = document.querySelectorAll(".color-dot");
      for (var i = 0; i < dots.length; i++) dots[i].classList.remove("selected");
      dot.classList.add("selected");

      currentColor = dot.getAttribute("data-color");

      // Update brush if pen is active
      if (currentTool === "pen" && canvas && canvas.freeDrawingBrush) {
        canvas.freeDrawingBrush.color = currentColor;
      }
    });

  // Thickness buttons
  document
    .getElementById("thickness-section")
    .addEventListener("click", function (e) {
      var btn = e.target.closest(".thickness-btn");
      if (!btn) return;

      // Update selection
      var buttons = document.querySelectorAll(".thickness-btn");
      for (var i = 0; i < buttons.length; i++)
        buttons[i].classList.remove("selected");
      btn.classList.add("selected");

      currentThickness = btn.getAttribute("data-thickness");

      // Update brush if pen is active
      if (currentTool === "pen" && canvas && canvas.freeDrawingBrush) {
        canvas.freeDrawingBrush.width = getThickness();
      }
    });

  // ── Keyboard Shortcuts ──

  document.addEventListener("keydown", function (e) {
    // Cmd+Shift+Z = redo (check before Cmd+Z since Shift changes key to "Z")
    if (e.metaKey && e.shiftKey && (e.key === "z" || e.key === "Z")) {
      e.preventDefault();
      redo();
      return;
    }
    // Cmd+Z = undo
    if (e.metaKey && !e.shiftKey && e.key === "z") {
      e.preventDefault();
      undo();
      return;
    }

    // Esc = cancel (but not while editing text)
    if (e.key === "Escape") {
      // If editing text, just exit editing mode
      var activeObj = canvas && canvas.getActiveObject();
      if (activeObj && activeObj.isEditing) {
        activeObj.exitEditing();
        return;
      }
      wz.send("cancel");
      return;
    }

    // Enter = confirm (but not while editing text)
    if (e.key === "Enter") {
      var activeObj2 = canvas && canvas.getActiveObject();
      if (activeObj2 && activeObj2.isEditing) {
        // Let Enter work normally in text editing
        return;
      }
      wz.send("confirm");
      return;
    }
  });

  // ── Cleanup (prevent memory leaks) ──

  function cleanupCanvas() {
    if (canvas) {
      canvas.dispose();
      canvas = null;
    }
    bgImage = null;
    if (hiddenCanvas) {
      hiddenCanvas = null;
    }
    hiddenCtx = null;
    stateStack = [];
    mosaicState = null;
  }

  // Expose to global scope so Python can call via eval_js
  window.cleanupCanvas = cleanupCanvas;

  window.addEventListener("beforeunload", cleanupCanvas);
})();
