if vim.g.loaded_cairn then
  return
end
vim.g.loaded_cairn = 1

require('cairn').setup()

vim.api.nvim_create_user_command('CairnQueue', function(opts)
  require('cairn.commands').queue(opts.args, opts.bang)
end, { nargs = '+', bang = true })

vim.api.nvim_create_user_command('CairnAccept', function()
  require('cairn.commands').accept()
end, {})

vim.api.nvim_create_user_command('CairnReject', function()
  require('cairn.commands').reject()
end, {})

vim.api.nvim_create_user_command('CairnPreview', function()
  require('cairn.commands').preview()
end, {})

vim.api.nvim_create_user_command('CairnListTasks', function()
  require('cairn.commands').list_tasks()
end, {})

vim.api.nvim_create_user_command('CairnListAgents', function()
  require('cairn.commands').list_agents()
end, {})

vim.api.nvim_create_user_command('CairnSelectAgent', function(opts)
  require('cairn.commands').select_agent(opts.args)
end, { nargs = 1 })
