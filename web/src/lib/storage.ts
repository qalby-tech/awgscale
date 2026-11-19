/** A set of strings remembered in this browser (which cards are unfolded, and the like). */
export class PersistedSet {
  private items: Set<string> | null = null;

  constructor(private readonly key: string) {}

  private load(): Set<string> {
    if (this.items) return this.items;
    let list: unknown = [];
    try {
      list = JSON.parse(localStorage.getItem(this.key) || "[]");
    } catch {
      /* no storage: this visit only */
    }
    this.items = new Set(Array.isArray(list) ? list.filter((item): item is string => typeof item === "string") : []);
    return this.items;
  }

  private save(): void {
    try {
      localStorage.setItem(this.key, JSON.stringify([...this.load()]));
    } catch {
      /* it still works for this visit */
    }
  }

  has(item: string): boolean {
    return this.load().has(item);
  }
  values(): string[] {
    return [...this.load()];
  }
  add(item: string): void {
    this.load().add(item);
    this.save();
  }
  delete(item: string): void {
    this.load().delete(item);
    this.save();
  }
  toggle(item: string): void {
    if (this.has(item)) this.delete(item);
    else this.add(item);
  }
  /** Drop what is no longer there, without writing: the next real change saves. */
  keepOnly(alive: (item: string) => boolean): void {
    for (const item of this.values()) if (!alive(item)) this.load().delete(item);
  }
}
