local function reload(name)
  package.loaded[name] = nil
  return require(name)
end

describe('cairn setup configuration and keymaps', function()
  local watcher_calls

  before_each(function()
    watcher_calls = { setup = 0, start = 0 }
    package.loaded['cairn.watcher'] = {
      setup = function()
        watcher_calls.setup = watcher_calls.setup + 1
      end,
      start = function()
        watcher_calls.start = watcher_calls.start + 1
      end,
    }

    reload('cairn.config')
    reload('cairn')
  end)

  it('merges config while preserving defaults', function()
    local cairn = require('cairn')
    cairn.setup({
      cairn_home = '/tmp/cairn-custom',
      preview_same_location = false,
    })

    assert.are.equal('/tmp/cairn-custom', cairn.config.cairn_home)
    assert.are.equal(false, cairn.config.preview_same_location)
    assert.are.equal(true, cairn.config.ghost_text)
    assert.are.equal(1, watcher_calls.setup)
    assert.are.equal(1, watcher_calls.start)
  end)

  it('sets default keymaps', function()
    local cairn = require('cairn')
    cairn.setup()

    local accept = vim.fn.maparg('<leader>a', 'n', false, true)
    local reject = vim.fn.maparg('<leader>r', 'n', false, true)
    local preview = vim.fn.maparg('<leader>p', 'n', false, true)

    assert.are.equal('<leader>a', accept.lhs)
    assert.are.equal('<leader>r', reject.lhs)
    assert.are.equal('<leader>p', preview.lhs)
  end)
end)
