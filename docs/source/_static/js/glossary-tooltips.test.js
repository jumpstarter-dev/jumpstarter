// Minimal test runner (no external deps) for the pure placement helper.
// Run with: node glossary-tooltips.test.js
"use strict";

var passed = 0;
var failed = 0;

function assert(label, condition) {
    if (condition) {
        console.log("PASS: " + label);
        passed++;
    } else {
        console.error("FAIL: " + label);
        failed++;
    }
}

// --- inline the function under test ---
// Returns a px offset (relative to span's left edge) that keeps the tooltip
// within [0, viewportWidth]. Negative shifts tooltip left; positive shifts right.
function tooltipOffset(spanLeft, tooltipMaxWidth, viewportWidth) {
    var offset = 0;
    offset = Math.min(offset, viewportWidth - spanLeft - tooltipMaxWidth);
    offset = Math.max(offset, -spanLeft);
    return offset;
}

var MAX_W = 400;

assert(
    "term well inside viewport: no adjustment",
    tooltipOffset(50, MAX_W, 1000) === 0
);

assert(
    "term exactly at right cutoff: no adjustment",
    tooltipOffset(600, MAX_W, 1000) === 0
);

assert(
    "term one pixel past right cutoff: shift left by 1",
    tooltipOffset(601, MAX_W, 1000) === -1
);

assert(
    "term near right edge: shift left enough to fit",
    tooltipOffset(700, MAX_W, 1000) === -100
);

assert(
    "term at left edge: no leftward shift (tooltip already at 0)",
    tooltipOffset(0, MAX_W, 1000) === 0
);

assert(
    "term near left, tooltip wider than remaining viewport: clamp at left edge",
    tooltipOffset(10, MAX_W, 300) === -10
);

assert(
    "tooltip wider than viewport, span at left edge: no shift possible",
    tooltipOffset(0, MAX_W, 300) === 0
);

console.log("\n" + passed + " passed, " + failed + " failed");
if (failed > 0) process.exit(1);
