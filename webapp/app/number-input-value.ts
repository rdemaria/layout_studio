// Decimal place values, including the useful range of JavaScript numbers.
export const MIN_DIGIT_PLACE = -323;
export const MAX_DIGIT_PLACE = 308;

export function initialDigitPlace(step: number | "any") {
  return typeof step === "number" && Number.isFinite(step) && step > 0
    ? Math.max(MIN_DIGIT_PLACE, Math.min(MAX_DIGIT_PLACE, Math.floor(Math.log10(step))))
    : 0;
}

export function parseNumberDraft(text: string): number | null {
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text.trim())) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function decimalParts(value: number) {
  const [mantissa, exponent = "0"] = String(value).split("e");
  const fractionLength = mantissa.split(".")[1]?.length ?? 0;
  return {
    coefficient: BigInt(mantissa.replace(".", "")),
    exponent: Number(exponent) - fractionLength,
  };
}

export function stepNumberAtPlace(value: number, place: number, direction: -1 | 1, min?: number) {
  // Add decimal integers before converting back to Number. This avoids both
  // 0.1 + 0.2 noise and snapping away digits finer than the selected place.
  const { coefficient, exponent } = decimalParts(value);
  const commonExponent = Math.min(exponent, place);
  const sum = coefficient * BigInt(10) ** BigInt(exponent - commonExponent)
    + BigInt(direction) * BigInt(10) ** BigInt(place - commonExponent);
  const next = Number(`${sum}e${commonExponent}`);
  return Number.isFinite(next) ? Math.max(min ?? -Infinity, next) : value;
}

export function numberAtPlace(value: number, place: number) {
  const sign = value < 0 ? "-" : "";
  const [mantissa, exponent = "0"] = String(Math.abs(value)).split("e");
  const [whole, fraction = ""] = mantissa.split(".");
  const digits = whole + fraction;
  const decimalIndex = whole.length + Number(exponent);
  let integer = decimalIndex <= 0 ? "0" : digits.slice(0, decimalIndex).padEnd(decimalIndex, "0");
  let decimals = decimalIndex < 0
    ? "0".repeat(-decimalIndex) + digits
    : digits.slice(decimalIndex);
  integer = integer.padStart(place + 1, "0");
  decimals = decimals.padEnd(Math.max(0, -place), "0");
  return {
    text: sign + integer + (decimals ? `.${decimals}` : ""),
    digitIndex: sign.length + integer.length - 1 - place + (place < 0 ? 1 : 0),
  };
}

export function digitIndexInDraft(text: string, place: number) {
  if (parseNumberDraft(text) === null) return -1;
  const start = text.length - text.trimStart().length;
  const [mantissa, exponent = "0"] = text.trim().toLowerCase().split("e");
  const decimalIndex = mantissa.includes(".") ? mantissa.indexOf(".") : mantissa.length;
  const relativePlace = place - Number(exponent);
  const index = decimalIndex - 1 - relativePlace + (relativePlace < 0 ? 1 : 0);
  return /\d/.test(mantissa[index] ?? "") ? start + index : -1;
}
