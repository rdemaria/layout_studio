import type {LayoutData} from "./layout-data";

/** Copy only changed JSON branches; a numeric edit must not clone 160,000 objects. */
export function editLayout(layout: LayoutData, edit: (draft: LayoutData) => void): LayoutData {
  type RecordValue = Record<PropertyKey, unknown>;
  type State = {base: RecordValue; copy?: RecordValue; children: Map<PropertyKey, State>; proxy: RecordValue};
  const states = new WeakMap<object, State>();
  const shallow = (base: RecordValue): RecordValue => (Array.isArray(base) ? base.slice() : {...base}) as RecordValue;
  const draft = (base: RecordValue): State => {
    const state = {base, children: new Map()} as State;
    state.proxy = new Proxy(base, {
      get(_target,key) {
        const value = (state.copy ?? base)[key];
        if (!value || typeof value !== "object") return value;
        if (states.has(value)) return value;
        const existing = state.children.get(key);
        if (existing?.base === value) return existing.proxy;
        const child = draft(value as RecordValue); state.children.set(key, child); return child.proxy;
      },
      set(_target,key,value) {
        (state.copy ??= shallow(base))[key] = value;
        state.children.delete(key); return true;
      },
      deleteProperty(_target,key) {delete (state.copy ??= shallow(base))[key]; state.children.delete(key); return true;},
      ownKeys: () => Reflect.ownKeys(state.copy ?? base),
      has: (_target,key) => key in (state.copy ?? base),
      getOwnPropertyDescriptor: (_target,key) => Object.getOwnPropertyDescriptor(state.copy ?? base,key),
    });
    states.set(state.proxy,state); return state;
  };
  const finalizeValue = (value: unknown): unknown => {
    if (!value || typeof value !== "object") return value;
    const state = states.get(value);
    if (state) return finish(state);
    // Assigned new containers may themselves contain draft children (e.g. map/rename).
    return value;
  };
  const finish = (state: State): RecordValue => {
    for (const [key,child] of state.children) {
      const value = finish(child);
      if (value !== child.base) (state.copy ??= shallow(state.base))[key] = value;
    }
    if (!state.copy) return state.base;
    const unwrap = (value: unknown, original?: unknown): unknown => {
      if (value === original || !value || typeof value !== "object") return value;
      if (states.has(value)) return finalizeValue(value);
      const copy = shallow(value as RecordValue);
      for (const key of Object.keys(copy)) copy[key] = unwrap(copy[key], (original as RecordValue | undefined)?.[key]);
      return copy;
    };
    for (const key of Object.keys(state.copy)) state.copy[key] = unwrap(state.copy[key], state.base[key]);
    return state.copy;
  };
  const root = draft(layout as unknown as RecordValue);
  edit(root.proxy as unknown as LayoutData);
  return finish(root) as unknown as LayoutData;
}
