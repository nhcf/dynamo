# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os

if "PYTHONHASHSEED" not in os.environ:
    os.environ["PYTHONHASHSEED"] = "0"

if __name__ == "__main__":
    from .main import main

    main()
