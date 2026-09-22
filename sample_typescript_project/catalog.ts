import { Repository } from "./repository";

/** Constructor-injected dependency: this.repo's type comes from the parameter property. */
export class Catalog {
  constructor(private repo: Repository) {}

  lookup(name: string) {
    return this.repo.find(name);
  }
}
