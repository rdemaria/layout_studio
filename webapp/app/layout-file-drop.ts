/** Only file drags are intercepted; text drags and editor interactions are untouched. */
export function installLayoutFileDrop(
  target: Window,
  onFile: (file: File) => void,
  onActive: (active: boolean) => void,
  onError: (message: string) => void,
): () => void {
  let depth = 0;
  const files = (event: DragEvent) => event.dataTransfer?.types.includes("Files");
  const reset = () => {depth = 0; onActive(false);};
  const enter = (event: DragEvent) => {
    if (!files(event)) return;
    event.preventDefault();
    depth += 1;
    onActive(true);
  };
  const over = (event: DragEvent) => {
    if (!files(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  };
  const leave = (event: DragEvent) => {
    if (!files(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) reset();
  };
  const drop = (event: DragEvent) => {
    if (!files(event)) return;
    event.preventDefault();
    reset();
    const dropped = Array.from(event.dataTransfer?.files ?? []);
    if (dropped.length !== 1) {onError("Drop one layout JSON file at a time."); return;}
    if (!/\.json$/i.test(dropped[0].name)) {onError("Choose a .json layout file."); return;}
    onFile(dropped[0]);
  };
  target.addEventListener("dragenter", enter);
  target.addEventListener("dragover", over);
  target.addEventListener("dragleave", leave);
  target.addEventListener("drop", drop);
  target.addEventListener("dragend", reset);
  target.addEventListener("blur", reset);
  return () => {
    target.removeEventListener("dragenter", enter);
    target.removeEventListener("dragover", over);
    target.removeEventListener("dragleave", leave);
    target.removeEventListener("drop", drop);
    target.removeEventListener("dragend", reset);
    target.removeEventListener("blur", reset);
  };
}
