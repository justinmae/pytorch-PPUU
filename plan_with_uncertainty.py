import argparse, os
import random
import torch
import numpy
import gym
import pdb
import importlib
import models
import utils
from dataloader import DataLoader
from torch.autograd import Variable


parser = argparse.ArgumentParser()
parser.add_argument('-map', type=str, default='i80')
parser.add_argument('-v', type=str, default='3')
parser.add_argument('-seed', type=int, default=333333)
# planning params
parser.add_argument('-method', type=str, default='bprop')
parser.add_argument('-batch_size', type=int, default=1)
parser.add_argument('-n_batches', type=int, default=200)
parser.add_argument('-lrt', type=float, default=0.01)
parser.add_argument('-ncond', type=int, default=20)
parser.add_argument('-npred', type=int, default=20)
parser.add_argument('-nexec', type=int, default=1)
parser.add_argument('-n_rollouts', type=int, default=10)
parser.add_argument('-rollout_length', type=int, default=1)
parser.add_argument('-bprop_niter', type=int, default=5)
parser.add_argument('-bprop_lrt', type=float, default=1.0)
parser.add_argument('-bprop_buffer', type=int, default=1)
parser.add_argument('-bprop_save_opt_stats', type=int, default=1)
parser.add_argument('-n_dropout_models', type=int, default=10)
parser.add_argument('-opt_z', type=int, default=0)
parser.add_argument('-opt_a', type=int, default=1)
parser.add_argument('-u_reg', type=float, default=0.0)
parser.add_argument('-graph_density', type=float, default=0.001)
parser.add_argument('-display', type=int, default=0)
parser.add_argument('-debug', type=int, default=0)
parser.add_argument('-model_dir', type=str, default='/misc/vlgscratch4/LecunGroup/nvidia-collab/models_v8/')
parser.add_argument('-mfile', type=str, default='model=fwd-cnn-ten3-layers=3-bsize=64-ncond=20-npred=20-lrt=0.0001-nfeature=256-nhidden=128-fgeom=1-zeroact=0-zmult=0-dropout=0.1-nz=32-beta=0.0-zdropout=0.5-gclip=5.0-warmstart=1-seed=1.model')
parser.add_argument('-value_model', type=str, default='model=value-bsize=64-ncond=20-npred=200-lrt=0.0001-nhidden=256-nfeature=256-gclip=10-dropout=0.1-gamma=0.99.model')
parser.add_argument('-policy_model_il', type=str, default='model=policy-il-mdn-bsize=64-ncond=20-npred=1-lrt=0.0001-nhidden=256-nfeature=256-nmixture=1-gclip=50.model')
parser.add_argument('-policy_model_tm', type=str, default='mbil-policy-gauss-nfeature=256-npred=1-lambdac=0.0-gamma=0.99-seed=1-model=fwd-cnn-ten3-zdropout=0.5.model')
#parser.add_argument('-mfile', type=str, default='model=policy-cnn-mdn-bsize=64-ncond=20-npred=1-lrt=0.0001-nhidden=256-nfeature=256-nmixture=1-gclip=10.model')

opt = parser.parse_args()

random.seed(opt.seed)
numpy.random.seed(opt.seed)
torch.manual_seed(opt.seed)

opt.save_dir = opt.model_dir + '/planning_results/'
opt.height = 117
opt.width = 24
opt.h_height = 14
opt.h_width = 3
opt.opt_z = (opt.opt_z == 1)
opt.opt_a = (opt.opt_a == 1)

def load_models():
    stats = torch.load('/misc/vlgscratch4/LecunGroup/nvidia-collab/traffic-data-atcold/data_i80_v0/data_stats.pth')
    forward_model = torch.load(opt.model_dir + opt.mfile)['model']
    value_function, policy_network_il, policy_network_mbil = None, None, None
    if opt.value_model != '':
        value_function = torch.load(opt.model_dir + f'/value_functions/{opt.value_model}').cuda()
        forward_model.value_function = value_function
    if opt.policy_model_il != '':
        policy_network_il = torch.load(opt.model_dir + f'/policy_networks/{opt.policy_model_il}').cuda()
        policy_network_il.stats = stats
    if opt.policy_model_tm != '':
        policy_network_mbil = torch.load(opt.model_dir + f'/policy_networks/{opt.policy_model_tm}')['model']
        policy_network_mbil.stats = stats
        forward_model.policy_net = policy_network_mbil.policy_net
        forward_model.policy_net.stats = stats
    
    forward_model.intype('gpu')
    forward_model.stats=stats
    if 'ten' in opt.mfile and False:
        forward_model.p_z = torch.load(opt.model_dir + opt.mfile + '.pz')
    return forward_model, value_function, policy_network_il, policy_network_mbil, stats

forward_model, value_function, policy_network_il, policy_network_mbil, data_stats = load_models()

if opt.u_reg > 0.0:
    forward_model.train()
    forward_model.value_function.train()
    dataloader = DataLoader(None, opt, opt.map)
    forward_model.estimate_uncertainty_stats(dataloader, n_batches=50, npred=opt.npred)



gym.envs.registration.register(
    id='I-80-v1',
    entry_point='map_i80_ctrl:ControlledI80',
    kwargs={'fps': 10, 'nb_states': opt.ncond, 'display': 0, 'delta_t': 0.1},
)

print('Building the environment (loading data, if any)')
env_names = {
    'i80': 'I-80-v1',
}

env = gym.make(env_names[opt.map])

dataloader = DataLoader(None, opt, 'i80')
splits = torch.load('/home/mbhenaff/scratch/traffic-data-atcold/data_i80_v0/splits.pth')

plan_file = opt.method
plan_file += f'-nbatches={opt.n_batches}-nexec={opt.nexec}'

if 'bprop' in opt.method:
    plan_file += f'-rollouts={opt.n_rollouts}-rollout_length={opt.npred}-lrt={opt.bprop_lrt}-niter={opt.bprop_niter}-ureg={opt.u_reg}-n_dropout={opt.n_dropout_models}=abuffer={opt.bprop_buffer}-saveoptstats={opt.bprop_save_opt_stats}'
print('[saving to {}/{}]'.format(opt.save_dir, plan_file))

times_to_collision = []
n_test = len(splits['test_indx'])
for j in range(n_test):
    movie_dir = '{}/videos_simulator/{}/ep{}/'.format(opt.save_dir, plan_file, j)
    print('[new episode, will save to: {}]'.format(movie_dir))
    car_path = dataloader.ids[splits['test_indx'][j]]
    timeslot, car_id = utils.parse_car_path(car_path)
    env.reset(time_slot=timeslot, vehicle_id=car_id)  # if None => picked at random
    forward_model.reset_action_buffer(opt.npred)
    inputs, done, mu, std = None, None, None, None
    images, states, costs, actions, mu_list, std_list = [], [], [], [], [], []
    cntr = 0
    while not done: 
        if inputs is None:
            print('[finding valid input]')
            while inputs is None:
                inputs, cost, done, info = env.step(numpy.zeros((2,)))
            print('[done]')
        input_images, input_states = inputs[0].contiguous(), inputs[1].contiguous()
        if opt.method == 'no-action':
            a = numpy.zeros((1, 2))
        elif opt.method == 'bprop':
            a = forward_model.plan_actions_backprop(input_images, input_states, npred=opt.npred, n_futures=opt.n_rollouts, normalize=True, bprop_niter = opt.bprop_niter, bprop_lrt = opt.bprop_lrt, u_reg=opt.u_reg, use_action_buffer=(opt.bprop_buffer==1), n_models=opt.n_dropout_models, save_opt_stats=(opt.bprop_save_opt_stats==1), nexec=opt.nexec)
        elif opt.method == 'policy-il':
            _, _, _, a = policy_network_il(input_images, input_states, sample=True, normalize_inputs=True, normalize_outputs=True)
            a = a.squeeze().cpu().view(1, 2).numpy()
        elif opt.method == 'policy-tm':
            a, entropy, mu, std = forward_model.policy_net(input_images, input_states, sample=True, normalize_inputs=True, normalize_outputs=True)
            a = a.cpu().view(1, 2).numpy()
        elif opt.method == 'bprop+policy-il':
            _, _, _, a = policy_network_il(input_images, input_states, sample=True, normalize_inputs=True, normalize_outputs=False)
            a = a[0]
            a = forward_model.plan_actions_backprop(input_images, input_states, npred=opt.npred, n_futures=opt.n_rollouts, normalize=True, bprop_niter = opt.bprop_niter, bprop_lrt = opt.bprop_lrt, actions=a, u_reg=opt.u_reg, nexec=opt.nexec)

            
        cntr += 1
        cost_test = 0
        t = 0
        T = opt.npred if opt.nexec == -1 else opt.nexec
        while (t < T) and not done:
            inputs, cost, done, info = env.step(a[t])
            if info.collisions_per_frame > 0:
                print(f'[collision after {cntr} frames, ending]')
                done = True

            print('(action: ({:.4f}, {:.4f}) | true costs: ({:.4f}, {:.4f})]'.format(a[t][0], a[t][1], cost[0][-1], cost[1][-1]))

            images.append(input_images[-1])
            states.append(input_states[-1])
            costs.append([cost[0][-1].item(), cost[1][-1].item()])
            if opt.mfile == 'no-action':
                actions.append(a[t])
                mu_list.append(mu)
                std_list.append(std)
            else:
                actions.append(((a[t]-data_stats['a_mean'].numpy())/data_stats['a_std']))
                if mu is not None:
                    mu_list.append(mu.data.cpu().numpy())
                    std_list.append(std.data.cpu().numpy())
            t += 1
        costs_ = numpy.stack(costs)
        if (len(images) > 600) or (opt.nexec == -1):
            done = True

    times_to_collision.append(len(images))
    utils.log(opt.save_dir + '/' + plan_file + '.log', 'ep: {} | time: {} | mean perf: {}'.format(j, len(images), torch.Tensor(times_to_collision).mean()))
    images = torch.stack(images)
    states = torch.stack(states)
    costs = torch.tensor(costs)
    actions = torch.stack(actions)
    '''
    images = numpy.stack(images).transpose(0, 2, 3, 1)
    states = numpy.stack(states)
    costs = numpy.stack(costs)
    actions = numpy.stack(actions)
    '''

    if mu is not None:
        mu_list = numpy.stack(mu_list)
        std_list = numpy.stack(std_list)
    else:
        mu_list, std_list = None, None
    utils.save_movie('{}/real/'.format(movie_dir), images.float() / 255.0, states, costs, actions=actions, mu=mu_list, std=std_list, pytorch=True)
    '''
    if 'ten' in opt.mfile and ('mbil' not in opt.mfile):
        for i in range(opt.n_rollouts):
            utils.save_movie('{}/imagined/rollout{}/'.format(movie_dir, i), pred[0][i].data, pred[1][i].data, pred[2][i].data, pytorch=True)
    '''

mean_time_to_collision = torch.Tensor(times_to_collision).mean()
median_time_to_collision = torch.Tensor(times_to_collision).median()
utils.log(opt.save_dir + '/' + plan_file + '.log', 'mean: {}'.format(mean_time_to_collision.item()))
utils.log(opt.save_dir + '/' + plan_file + '.log', 'median: {}'.format(median_time_to_collision.item()))
torch.save(torch.Tensor(times_to_collision), opt.save_dir + '/' + plan_file + '.pth')