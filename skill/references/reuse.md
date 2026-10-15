## Start from what already exists

Before writing a recipe, look for one. Tacto ships recipes for common tasks, and
replaying one costs nothing:

```bash
tacto list                                  # built-in and saved recipes
tacto show textedit_write_note              # its steps, and its inputs under "params"
tacto replay textedit_new_document --helper --yes
tacto replay textedit_write_note -p text="회의 준비" --helper --yes
```

Writing into a document ends `unverified — … 확인 영수증이 없습니다` when the
document is not saved: the text was sent, and with no file there is nothing to
read back. That is expected — confirm with `tacto observe <app> --helper` and move on.

The longer guides live beside this file: [Flow reference](../../docs/recipes.md) (recipes, decisions,
branches) and [API reference](../../docs/api.md).


## Turning a one-off into a reusable recipe

If you already wrote a concrete recipe, promote it instead of rewriting:

```bash
tacto parameterize flows/calc_12x34.json -p 12=a -p 34=b -p 408=result \
    --digits --derived='result={{a}} * {{b}}'
```

If the agent **walked** a task (`tacto do` with no recipe), the verified walk is
already filed as a candidate. Promote that instead of writing one:

```bash
tacto candidates                      # candidates, the values that came from the request,
                                    # and why one cannot become a recipe yet
tacto promote <name> -p 25=n          # 25 becomes the input n; judgment points are
                                    # reviewed once and kept only if the walk supports them
tacto promote <name> --no-decisions   # skip that review
```

A recipe promoted from a walk may re-aim a missing target once (`--think 0` to
forbid it), because it recorded labels the way the screen showed them that time.
