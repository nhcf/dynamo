        Excludes placeholder entries (id=-1) that are reserved during load but not yet committed.
        """
        return {
            name: lora.id
            for name, lora in self.loaded_loras.items()
            if lora.id != -1  # Skip uncommitted placeholder reservations
        }
