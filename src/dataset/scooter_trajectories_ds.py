import pandas as pd
import numpy as np
import os
import sys
import time
import json

from util.log import Log
from util.util import get_elapsed, unzip
from util.constant import DATA_FOLDER

from .constant import ScooterTrajectoriesC as C

log = Log(__name__, enable_console=True, enable_file=False)


class ScooterTrajectoriesDS:
    def __init__(self, zip_filepath=None, log_lvl=None):
        if zip_filepath:
            self.zip_filepath = zip_filepath
        else:
            self.zip_filepath = os.path.join(DATA_FOLDER, C.ZIP_DEFAULT_FN)

        if log_lvl is not None:
            if not isinstance(log_lvl, int):
                raise ValueError("Invalid log level: {}".format(log_lvl))
            log.set_level(log_lvl)

        self.unzip_folder = os.path.splitext(self.zip_filepath)[0]
        self.merge = pd.DataFrame(columns=C.MERGE_COLS)
        self.dataset = pd.DataFrame(columns=C.DATASET_COLS)
        self.rental = pd.DataFrame(columns=C.RENTAL_COLS)
        self.pos = pd.DataFrame(columns=C.POS_GEN_COLS)
        self.moving_behavior_features = pd.DataFrame()

    def __unzip(self):
        if not os.path.exists(self.zip_filepath):
            log.f("Unzip path not exist")
            return

        unzip(self.zip_filepath)

    def __unzip_generated(self):
        if not os.path.exists(os.path.join(DATA_FOLDER, C.GENERATED_DN + ".zip")):
            log.f("Unzip path not exist")
            return

        unzip(os.path.join(DATA_FOLDER, C.GENERATED_DN + ".zip"))

    def __is_pos_timestamp_in_rental(self, pos, rental):
        pos_server_time_cn = C.POS_TIME_COLS[0]
        pos_device_time_cn = C.POS_TIME_COLS[1]
        rental_start_time_cn = C.RENTAL_TIME_COLS[0]
        rental_end_time_cn = C.RENTAL_TIME_COLS[1]

        return (pos[pos_server_time_cn] >= rental[rental_start_time_cn]) & \
               (pos[pos_device_time_cn] >= rental[rental_start_time_cn]) & \
               (pos[pos_server_time_cn] <= rental[rental_end_time_cn]) & \
               (pos[pos_device_time_cn] <= rental[rental_end_time_cn])

    def __parse_pos_datetime(self, df):
        for date_cn in C.POS_TIME_COLS:
            df[date_cn] = pd.to_datetime(df[date_cn])
        return df

    def __parse_rental_datetime(self, df):
        for date_cn in C.RENTAL_TIME_COLS:
            df[date_cn] = pd.to_datetime(df[date_cn])
        return df

    def __load_device_data(self):
        if not os.path.exists(self.unzip_folder):
            log.e("Scooter Trajectories folder not extracted: impossible to load device data")
            return pd.DataFrame(), get_elapsed(0, 0)

        start = time.time()
        # Parse device data
        device_file = os.path.join(self.unzip_folder, C.CSV_DEVICE_FN)
        device_data = pd.read_csv(device_file, memory_map=True, names=C.DEVICE_COLS, header=0)
        end = time.time()

        return device_data, get_elapsed(start, end)

    def __load_pos_data(self, chunknum=None, chunksize=50000, max_chunknum=None):
        if not os.path.exists(self.unzip_folder):
            log.e("Scooter Trajectories folder not extracted: impossible to load position data")
            return pd.DataFrame(), get_elapsed(0, 0)

        start = time.time()
        pos_data = pd.DataFrame()

        # Parse device data
        curr_chunk = 0
        for fn in C.CSV_POS_FN:
            header = 0  # if fn == self.CSV_POS_FN[0] else None
            pos_file = os.path.join(self.unzip_folder, fn)
            reader = pd.read_csv(pos_file, names=C.POS_COLS, header=header, chunksize=chunksize,
                                 iterator=True, memory_map=True)
            for pos_chunk_df in reader:
                if chunknum is not None:
                    if chunknum == curr_chunk:
                        end = time.time()
                        reader.close()
                        return self.__parse_pos_datetime(pos_chunk_df), get_elapsed(start, end)
                else:
                    # Load all chunk
                    pos_data = pd.concat([pos_data, pos_chunk_df], axis=0)

                curr_chunk += 1

                if max_chunknum == curr_chunk:
                    end = time.time()
                    return self.__parse_pos_datetime(pos_data), get_elapsed(start, end)

            reader.close()

        end = time.time()
        return self.__parse_pos_datetime(pos_data), get_elapsed(start, end)

    def __load_rental_data(self):
        if not os.path.exists(self.unzip_folder):
            log.e("Scooter Trajectories folder not extracted: impossible to load rental data")
            return pd.DataFrame(), get_elapsed(0, 0)

        start = time.time()
        # Parse device data
        rental_file = os.path.join(self.unzip_folder, C.CSV_RENTAL_FN)
        rental_data = pd.read_csv(rental_file, memory_map=True, names=C.RENTAL_COLS, header=0)
        rental_data = self.__parse_rental_datetime(rental_data)

        end = time.time()
        return rental_data, get_elapsed(start, end)

    def __load_user_data(self):
        if not os.path.exists(self.unzip_folder):
            log.e("Scooter Trajectories folder not extracted: impossible to load user data")
            return pd.DataFrame(), get_elapsed(0, 0)

        start = time.time()
        # Parse device data
        user_file = os.path.join(self.unzip_folder, C.CSV_USR_FN)
        user_data = pd.read_csv(user_file, memory_map=True, names=C.USR_COLS, header=0)

        end = time.time()
        return user_data, get_elapsed(start, end)

    def __generate(self, device_df, rental_df, user_df, chunknum=None, chunksize=50000, max_chunknum=None):
        if not os.path.exists(self.unzip_folder):
            log.e("Scooter Trajectories folder not extracted: impossible to load pos and rental map data")
            return pd.DataFrame(), get_elapsed(0, 0)

        start = time.time()
        gen_data = pd.DataFrame(columns=C.MERGE_COLS)

        # Parse device data
        curr_chunk = 0
        for fn in C.CSV_POS_FN:
            header = 0  # if fn == self.CSV_POS_FN[0] else None
            pos_file = os.path.join(self.unzip_folder, fn)
            reader = pd.read_csv(pos_file, names=C.POS_COLS, header=header, chunksize=chunksize, iterator=True,
                                 memory_map=True)

            for pos_chunk_df in reader:
                # Filter pos and rental data
                pos_chunk_df, rental_df, _ = self.__filter_rental_pos(pos_chunk_df, rental_df,
                                                                      user_df, device_df)

                # Combine positions to related rental
                # self.dataset = self.__map_pos_into_rental(rental_filtered_df, pos_filtered_df)
                # self.dataset = self.__map_rental_into_pos(rental_filtered_df, pos_filtered_df)
                # pos_rental_map_df, merge_elapsed_time = self.__merge_rental_into_pos(pos_filtered_df,
                #                                                                      rental_filtered_df)
                if chunknum is not None:
                    if chunknum == curr_chunk:
                        pos_chunk_df = self.__parse_pos_datetime(pos_chunk_df)
                        pos_rental_map_df, merge_elapsed_time = self.__merge(pos_chunk_df, rental_df,
                                                                             user_df, device_df)
                        log.d("__chunk {} merge elapsed time: {}".format(curr_chunk, merge_elapsed_time))
                        reader.close()
                        end = time.time()
                        return pos_rental_map_df, get_elapsed(start, end)
                else:
                    # Load all chunk
                    pos_chunk_df = self.__parse_pos_datetime(pos_chunk_df)
                    pos_rental_map_df, merge_elapsed_time = self.__merge(pos_chunk_df, rental_df,
                                                                         user_df, device_df)
                    log.d("__chunk {} merge elapsed time: {}".format(curr_chunk, merge_elapsed_time))
                    gen_data = pd.concat([gen_data, pos_rental_map_df], axis=0)

                    if max_chunknum is not None and max_chunknum == curr_chunk:
                        end = time.time()
                        return gen_data, get_elapsed(start, end)

                curr_chunk += 1

            reader.close()

        end = time.time()
        return gen_data, get_elapsed(start, end)

    def __build(self, gen_df, rental_df):
        start = time.time()
        # Calculate valid positions and rental according to the timestamp of generated data
        pos_df = gen_df[list(C.POS_GEN_COLS_MERGE_MAP.keys())]
        pos_df = pos_df.rename(columns=C.POS_GEN_COLS_MERGE_MAP)
        pos_df, rental_df = self.__find_data_map(gen_df, pos_df, rental_df)

        # Sort generated data, pos data and rental data
        pos_df = pd.DataFrame(pos_df, columns=C.POS_GEN_COLS)
        merge_df, pos_df, rental_df, _ = self.__sort(gen_df, pos_df, rental_df)

        # Build dataset
        dataset_group_cols = list(C.MERGE_COLS_RENTAL_MAP.values()) + list(C.MERGE_COLS_DEVICE_MAP.values()) + list(
            C.MERGE_COLS_USR_MAP.values())
        dataset_group_cols = list(dict.fromkeys(dataset_group_cols))
        groups_by_rental = merge_df.groupby(dataset_group_cols)
        dataset_df = groups_by_rental[C.MERGE_POS_ID_CN].apply(list).reset_index(name=C.DATASET_RENTAL_POSITIONS_CN)
        dataset_df = dataset_df[C.DATASET_COLS]

        end = time.time()
        return dataset_df, merge_df, pos_df, rental_df, get_elapsed(start, end)

    def __map_pos_into_rental(self, rental_df: pd.DataFrame, pos_df: pd.DataFrame):
        def ___find_pos_of_rental(p_df, rental, rental_idx):
            start = time.time()
            pos_of_rental = p_df.loc[
                (p_df[C.POS_DEVICE_ID_CN] == rental[C.RENTAL_DEVICE_ID_CN])
                & self.__is_pos_timestamp_in_rental(p_df, rental)]
            end = time.time()
            print("__rental {}; positions found: {}; time: {};"
                  .format(rental_idx, pos_of_rental.index.to_numpy(), get_elapsed(start, end)))
            return pos_of_rental

        rental_df[C.DATASET_RENTAL_POSITIONS_CN] = [
            ___find_pos_of_rental(pos_df, rental, rental_idx)
            for rental_idx, rental in rental_df.iterrows()]

        # Take only the rental with positions not empty
        not_empty_positions = [pos.size != 0 for pos in rental_df[C.DATASET_RENTAL_POSITIONS_CN]]
        rental_df = rental_df[not_empty_positions]
        return rental_df

    def __map_rental_into_pos(self, rental_df: pd.DataFrame, pos_df: pd.DataFrame):
        def ___find_rental_of_pos(r_df, pos, pos_idx):
            rental_of_pos = r_df.loc[
                (rental_df[C.RENTAL_DEVICE_ID_CN] == pos[C.POS_DEVICE_ID_CN])
                & self.__is_pos_timestamp_in_rental(pos, r_df)].to_numpy()
            if pos_idx % 1000 == 0:
                log.d("__position {:10} processed".format(pos_idx))
            return rental_of_pos

        pos_df[C.POS_RENTAL_CN] = [___find_rental_of_pos(rental_df, pos, pos_idx)
                                   for pos_idx, pos in pos_df.iterrows()]

        # Remove positions without rentals
        pos_df = pos_df[[rentals.size != 0 for rentals in pos_df[C.POS_RENTAL_CN]]]

        return pos_df

    def __merge(self, pos_df: pd.DataFrame, rental_df: pd.DataFrame, user_df: pd.DataFrame, device_df: pd.DataFrame):
        start = time.time()

        # Merge rental data with pos data
        rental_df = rental_df.rename(columns=C.MERGE_COLS_RENTAL_MAP)
        pos_df = pos_df.rename(columns=C.MERGE_COLS_POS_MAP)
        merged_df = rental_df.merge(pos_df, on=C.POS_DEVICE_ID_CN)

        # Filter pos data in rental timestamp range
        merged_df = merged_df.loc[
            self.__is_pos_timestamp_in_rental(merged_df[C.POS_TIME_COLS], merged_df[C.RENTAL_TIME_COLS])]

        # Merge rental data with usr data
        user_df = user_df.rename(columns=C.MERGE_COLS_USR_MAP)
        merged_df = merged_df.merge(user_df, on=C.RENTAL_USR_ID_CN)

        # Merge rental data with device data
        device_df = device_df.rename(columns=C.MERGE_COLS_DEVICE_MAP)
        merged_df = merged_df.merge(device_df, on=C.RENTAL_DEVICE_ID_CN)

        end = time.time()
        return merged_df, get_elapsed(start, end)

    def __filter_rental_pos(self, pos_df, rental_df, user_df, device_df):
        start = time.time()
        # Filter rental according to user id and device id
        rental_filtered_df = rental_df.loc[rental_df[C.RENTAL_DEVICE_ID_CN].isin(device_df[C.DEVICE_ID_CN]) &
                                           rental_df[C.RENTAL_USR_ID_CN].isin(user_df[C.USR_ID_CN]) &
                                           rental_df[C.RENTAL_DEVICE_ID_CN].isin(pos_df[C.POS_DEVICE_ID_CN])]

        # Filter positions according to rentals
        pos_filtered_df = pos_df.loc[pos_df[C.POS_DEVICE_ID_CN].isin(rental_filtered_df[C.RENTAL_DEVICE_ID_CN])]

        end = time.time()
        return pos_filtered_df, rental_filtered_df, get_elapsed(start, end)

    def __find_data_map(self, rental_pos_map_df, pos_df, rental_df):
        rental_valid_df = rental_df.loc[rental_df[C.RENTAL_ID_CN].isin(rental_pos_map_df[C.MERGE_RENTAL_ID_CN])]
        pos_valid_df = pos_df.loc[pos_df[C.POS_GEN_ID_CN].isin(rental_pos_map_df[C.MERGE_POS_ID_CN])]
        return pos_valid_df, rental_valid_df

    def __find_timedelta(self, group_df, time_delta=None):
        pos_time_cols = [C.POS_GEN_SERVER_TIME_CN, C.POS_GEN_DEVICE_TIME_CN]
        time_columns_group = group_df[pos_time_cols].reset_index(drop=True)

        # Find the positions that have a distance between the previous position at least of time_distance
        prev_time_columns_group = time_columns_group.iloc[time_columns_group.index - 1].reset_index(drop=True)
        time_gaps = time_columns_group.subtract(prev_time_columns_group)
        time_gaps.iloc[0] = pd.Timedelta(0)

        # Calculate time_delta dynamically if None
        if time_delta is None:
            # Take the 50% + 34.1% of normal distribution
            time_delta = time_gaps.mean() + time_gaps.std().fillna(pd.Timedelta(0))
        time_gaps_map = time_gaps >= time_delta

        # Assign a different id for each time sequence
        time_gaps_map = time_gaps_map[pos_time_cols[0]] & time_gaps_map[pos_time_cols[1]]
        return time_gaps_map.ne(time_gaps_map.shift()).cumsum(), time_gaps

    def __find_spreaddelta(self, group_df, spread, spread_delta):
        map_df = (group_df >= (spread - spread_delta)) & (group_df <= (spread + spread_delta))
        return map_df[C.POS_GEN_SPREAD_LATITUDE_CN] & map_df[C.POS_GEN_SPREAD_LONGITUDE_CN]

    def __find_edgedelta(self, group_df, edge, edge_delta):
        map_df = (group_df >= (edge - edge_delta)) & (group_df <= (edge + edge_delta))
        return map_df[C.POS_GEN_EDGE_LATITUDE_START_CN] & map_df[C.POS_GEN_EDGE_LONGITUDE_START_CN] & \
            map_df[C.POS_GEN_EDGE_LATITUDE_STOP_CN] & map_df[C.POS_GEN_EDGE_LONGITUDE_STOP_CN]

    def __find_posdelta(self, group_df, pos, pos_delta):
        map_df = (group_df >= (pos - pos_delta)) & (group_df <= (pos + pos_delta))
        return map_df[C.POS_GEN_LATITUDE_CN] & map_df[C.POS_GEN_LONGITUDE_CN]

    def __load_support_data(self):
        start = time.time()
        device_df, _ = self.__load_device_data()
        rental_df, _ = self.__load_rental_data()
        user_df, _ = self.__load_user_data()
        end = time.time()
        return device_df, rental_df, user_df, get_elapsed(start, end)

    def __sort(self, rental_pos_map_df, pos_df, rental_df):
        start = time.time()
        # List column names for sorting
        merge_sort_cols = C.MERGE_SORT_COLS
        pos_sort_cols = C.POS_GEN_SORT_COLS
        rental_sort_cols = C.RENTAL_SORT_COLS

        # Sort generated merge
        rental_pos_map_df = rental_pos_map_df.sort_values(by=merge_sort_cols, ignore_index=True)
        pos_df = pos_df.sort_values(by=pos_sort_cols, ignore_index=True)
        rental_df = rental_df.sort_values(by=rental_sort_cols, ignore_index=True)

        # Columns sort
        rental_pos_map_df = rental_pos_map_df[C.MERGE_COLS]
        pos_df = pos_df[C.POS_GEN_COLS]
        rental_df = rental_df[C.RENTAL_COLS]

        end = time.time()
        return rental_pos_map_df, pos_df, rental_df, get_elapsed(start, end)

    def __moving_attributes(self, trajectory: pd.DataFrame, sw_width, sw_offset):
        traj_len = len(trajectory.index)
        sw_width, sw_offset = int(sw_width), int(sw_offset)
        i = 0

        trajectory_behavior = pd.DataFrame(columns=C.MOVING_BEHAVIOR_FEATURES_COLS)

        while i < traj_len:
            window = trajectory.iloc[i:i + sw_width][C.MOVING_ATTRIBUTES]

            if len(window.index) > 1:
                # Get moving attribute for this window.
                window_curr = window.iloc[1:len(window.index)].reset_index(drop=True)
                window_prev = window.iloc[0:len(window.index) - 1].reset_index(drop=True)
                moving_attributes = window_curr.subtract(window_prev)

                delta_t = moving_attributes[[C.POS_GEN_SERVER_TIME_CN, C.POS_GEN_DEVICE_TIME_CN]].mean(axis=1)
                delta_l = moving_attributes[[C.POS_GEN_LATITUDE_CN, C.POS_GEN_LONGITUDE_CN]]
                delta_s = moving_attributes[C.POS_GEN_SPEED_CN]

                # Calculate ROT (rate of turn)
                rot = np.arctan(moving_attributes[C.POS_GEN_LONGITUDE_CN] / moving_attributes[C.POS_GEN_LATITUDE_CN])
                rot = rot.fillna(0)
                rot = pd.concat([pd.Series(0), rot], axis=0, ignore_index=True)
                rot_curr = rot.iloc[1:len(rot.index)].reset_index(drop=True)
                rot_prev = rot.iloc[0:len(rot.index) - 1].reset_index(drop=True)
                delta_r = rot_curr.subtract(rot_prev)

                f_delta_l = pd.concat([delta_l[C.POS_GEN_LATITUDE_CN] / (delta_t.astype(np.int64)),
                                       delta_l[C.POS_GEN_LONGITUDE_CN] / (delta_t.astype(np.int64))], axis=1)
                f_delta_latitude = f_delta_l[f_delta_l.columns[0]]
                f_delta_longitude = f_delta_l[f_delta_l.columns[1]]
                f_delta_s = delta_s
                f_delta_r = delta_r

                window_mbf = [
                    # -- f_delta_latitude
                    f_delta_latitude.mean(), f_delta_latitude.max(), f_delta_latitude.quantile(0.75),
                    f_delta_latitude.quantile(0.50), f_delta_latitude.quantile(0.25), f_delta_latitude.min(),
                    # -- f_delta_longitude
                    f_delta_longitude.mean(), f_delta_longitude.max(), f_delta_longitude.quantile(0.75),
                    f_delta_longitude.quantile(0.50), f_delta_longitude.quantile(0.25), f_delta_longitude.min(),
                    # -- f_delta_s
                    f_delta_s.mean(), f_delta_s.max(), f_delta_s.quantile(0.75), f_delta_s.quantile(0.50),
                    f_delta_s.quantile(0.25), f_delta_s.min(),
                    # -- f_delta_r
                    f_delta_r.mean(), f_delta_r.max(), f_delta_r.quantile(0.75), f_delta_r.quantile(0.50),
                    f_delta_r.quantile(0.25), f_delta_r.min(),
                ]

                df_window_mbf = pd.concat([pd.DataFrame([elem]) for elem in window_mbf], axis=1, ignore_index=True)
                df_window_mbf = df_window_mbf.rename(columns=dict(zip(df_window_mbf.columns,
                                                                      C.MOVING_BEHAVIOR_FEATURES_COLS)))

                trajectory_behavior = pd.concat([trajectory_behavior, df_window_mbf], axis=0)

            if i + sw_width >= traj_len:
                break
            i += sw_offset

        return trajectory_behavior.reset_index(drop=True)

    def generate(self, chunknum=0, chunksize=50000):
        log.d("Scooter Trajectories start unzip")
        self.__unzip()

        log.d("Scooter Trajectories load device, rental, user data")
        device_df, rental_df, user_df, load_time = self.__load_support_data()
        log.d("elapsed time: {}".format(load_time))

        # Load dataset and positions according to chunk info
        log.d("Scooter Trajectories load pos and rental timestamp map data")
        gen_df, load_time = self.__generate(device_df, rental_df, user_df, chunknum=chunknum, chunksize=chunksize)
        log.d("elapsed time: {}".format(load_time))

        log.d("Scooter Trajectories build final data")
        self.dataset, self.merge, self.pos, self.rental, load_time = self.__build(gen_df, rental_df)
        log.d("elapsed time: {}".format(load_time))

        return self

    def generate_all(self, chunksize=50000, max_chunknum=None):
        log.d("Scooter Trajectories start unzip")
        self.__unzip()

        if not os.path.exists(self.unzip_folder):
            log.e("Data folder not exist")
            return self

        log.d("Scooter Trajectories load device, rental, user data")
        device_df, rental_df, user_df, load_time = self.__load_support_data()
        log.d("elapsed time: {}".format(load_time))

        log.d("Scooter Trajectories load pos and rental timestamp map data")
        gen_df, load_time = self.__generate(device_df, rental_df, user_df, chunknum=None,
                                            chunksize=chunksize, max_chunknum=max_chunknum)
        log.d("elapsed time: {}".format(load_time))

        log.d("Scooter Trajectories build final data")
        self.dataset, self.merge, self.pos, self.rental, load_time = self.__build(gen_df, rental_df)
        log.d("elapsed time: {}".format(load_time))

        return self

    def load_generated(self):
        log.d("Scooter Trajectories start unzip")
        self.__unzip_generated()

        if not os.path.exists(os.path.join(DATA_FOLDER, C.GENERATED_DN)):
            log.e("Generated folder not exist")
            return self

        log.d("Scooter Trajectories load generated")
        start = time.time()

        dataset_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_DATASET_GENERATED_FN)
        if os.path.exists(dataset_gen_fp):
            self.dataset = pd.read_csv(dataset_gen_fp, memory_map=True, parse_dates=C.DATASET_TIME_COLS,
                                       converters={C.DATASET_RENTAL_POSITIONS_CN: json.loads})
            self.dataset = pd.DataFrame(self.dataset, columns=C.DATASET_COLS)
        else:
            log.w("{} path not exist".format(dataset_gen_fp))

        merge_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_MERGE_GENERATED_FN)
        if os.path.exists(merge_gen_fp):
            self.merge = pd.read_csv(merge_gen_fp, parse_dates=C.MERGE_TIME_COLS,
                                     infer_datetime_format=True, memory_map=True)
            self.merge = pd.DataFrame(self.merge, columns=C.MERGE_COLS)
        else:
            log.w("{} path not exist".format(merge_gen_fp))

        pos_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_POS_GENERATED_FN)
        if os.path.exists(pos_gen_fp):
            self.pos = pd.read_csv(pos_gen_fp, parse_dates=C.POS_TIME_COLS,
                                   infer_datetime_format=True, memory_map=True)
            self.pos = pd.DataFrame(self.pos, columns=C.POS_GEN_COLS)
        else:
            log.w("{} path not exist".format(pos_gen_fp))

        rental_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_RENTAL_GENERATED_FN)
        if os.path.exists(rental_gen_fp):
            self.rental = pd.read_csv(rental_gen_fp, parse_dates=C.RENTAL_TIME_COLS,
                                      infer_datetime_format=True, memory_map=True)
            self.rental = pd.DataFrame(self.rental, columns=C.RENTAL_COLS)
        else:
            log.w("{} path not exist".format(rental_gen_fp))

        moving_behavior_feature_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_MOVING_BEHAVIOR_FEATURE)
        if os.path.exists(moving_behavior_feature_gen_fp):
            self.moving_behavior_features = pd.read_csv(moving_behavior_feature_gen_fp, memory_map=True)
        else:
            log.w("{} path not exist".format(moving_behavior_feature_gen_fp))

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))

        return self

    def to_csv(self):
        log.d("Scooter Trajectories to csv")
        start = time.time()

        if not os.path.exists(os.path.join(DATA_FOLDER, C.GENERATED_DN)):
            os.makedirs(os.path.join(DATA_FOLDER, C.GENERATED_DN))

        # Save data in csv files
        pos_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_POS_GENERATED_FN)
        if not self.pos.empty:
            self.pos.to_csv(pos_gen_fp, index=False)

        rental_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_RENTAL_GENERATED_FN)
        if not self.rental.empty:
            self.rental.to_csv(rental_gen_fp, index=False)

        merge_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_MERGE_GENERATED_FN)
        if not self.merge.empty:
            self.merge.to_csv(merge_gen_fp, index=False)

        dataset_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_DATASET_GENERATED_FN)
        if not self.dataset.empty:
            self.dataset.to_csv(dataset_gen_fp, index=False)

        moving_behavior_feature_gen_fp = os.path.join(DATA_FOLDER, C.GENERATED_DN, C.CSV_MOVING_BEHAVIOR_FEATURE)
        if not self.moving_behavior_features.empty:
            self.moving_behavior_features.to_csv(moving_behavior_feature_gen_fp, index=False)

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))

        return self

    def timedelta_heuristic(self, timedelta=None):
        log.d("Scooter Trajectories timedelta heuristic")
        start = time.time()

        timedelta = pd.Timedelta(timedelta) if timedelta is not None else None
        groups_by_rental = self.pos.groupby(by=[C.POS_GEN_RENTAL_ID_CN])

        timedelta_ids = []
        time_gaps = []
        for _, group in groups_by_rental:
            group_timedelta_ids, group_time_gaps = self.__find_timedelta(group, timedelta)
            timedelta_ids.extend(group_timedelta_ids)
            time_gaps.extend(group_time_gaps.astype("timedelta64[ms]").mean(axis=1))
            sys.stdout.write("\r {:.3f} %".format(len(timedelta_ids) * 100 / len(self.pos.index)))

        sys.stdout.write("\r")
        self.pos[C.POS_GEN_TIMEDELTA_ID_CN] = timedelta_ids
        self.pos[C.POS_GEN_TIME_GAP_CN] = time_gaps

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))
        return self

    def spreaddelta_heuristic(self, groupby, spreaddelta=None):
        log.d("Scooter Trajectories spreaddelta heuristic")
        start = time.time()
        delta = spreaddelta if spreaddelta is not None else None

        # Initialize column to null
        self.pos[C.POS_GEN_SPREADDELTA_ID_CN] = np.nan

        # Group position for the column specified by the user
        pos_groups = self.pos.groupby(by=groupby)
        # Calculate the spread of each position group
        spread_pos_groups_cols = [C.POS_GEN_SPREAD_LATITUDE_CN, C.POS_GEN_SPREAD_LONGITUDE_CN]
        spread_pos_groups = pos_groups[C.POS_GEN_COORD_COLS].apply(lambda x: x.max() - x.min())
        spread_pos_groups = spread_pos_groups.rename(columns=dict(zip(spread_pos_groups.columns,
                                                                      spread_pos_groups_cols)))
        if type(groupby) == list:
            pos_groups_as_index = pd.MultiIndex.from_frame(self.pos[groupby])
        else:
            pos_groups_as_index = self.pos[groupby]

        # Save spread in positions
        self.pos[spread_pos_groups_cols] = spread_pos_groups.loc[pos_groups_as_index].values

        # Calculate cluster spread id
        i = 1
        while self.pos[C.POS_GEN_SPREADDELTA_ID_CN].isnull().sum() != 0:
            # Find the mean spread
            nearest_mean = spread_pos_groups.iloc[
                (spread_pos_groups-spread_pos_groups.mean()).abs().sum(axis=1).argmin()]

            if spreaddelta is None:
                delta = spread_pos_groups.std().fillna(0.0) / 4  # Take the 20% of normal distribution

            # Assign index at the positions of the same spread cluster
            spread_cluster = spread_pos_groups.loc[self.__find_spreaddelta(spread_pos_groups, nearest_mean, delta)]
            self.pos.loc[pos_groups_as_index.isin(spread_cluster.index), C.POS_GEN_SPREADDELTA_ID_CN] = i

            # Remove groups already assigned and update the list
            spread_pos_groups = spread_pos_groups.drop(index=spread_cluster.index)
            sys.stdout.write("\r {:.3f} %".format(
                (pos_groups.ngroups - len(spread_pos_groups.index)) * 100 / pos_groups.ngroups))
            i += 1

        sys.stdout.write("\r")
        self.pos = self.pos.astype({C.POS_GEN_SPREADDELTA_ID_CN: "int64"})
        self.pos = self.pos[C.POS_GEN_COLS]

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))
        return self

    def edgedelta_heuristic(self, groupby, edgedelta=None):
        log.d("Scooter Trajectories edgedelta heuristic")
        start = time.time()
        delta = edgedelta if edgedelta is not None else None

        # Initialize column to null
        self.pos[C.POS_GEN_EDGEDELTA_ID_CN] = np.nan

        # Group position for the column specified by the user
        pos_groups = self.pos.groupby(by=groupby)
        # Calculate the edge for each position group
        edge_pos_groups_cols = [C.POS_GEN_EDGE_LATITUDE_START_CN, C.POS_GEN_EDGE_LONGITUDE_START_CN,
                                C.POS_GEN_EDGE_LATITUDE_STOP_CN, C.POS_GEN_EDGE_LONGITUDE_STOP_CN]
        edge_pos_groups = pos_groups[C.POS_GEN_COORD_COLS].apply(lambda x: pd.concat([x.iloc[0], x.iloc[-1]],
                                                                                     ignore_index=True))
        edge_pos_groups = edge_pos_groups.rename(columns=dict(zip(edge_pos_groups.columns, edge_pos_groups_cols)))

        if type(groupby) == list:
            pos_groups_as_index = pd.MultiIndex.from_frame(self.pos[groupby])
        else:
            pos_groups_as_index = self.pos[groupby]

        # Save edge in positions
        self.pos[edge_pos_groups_cols] = edge_pos_groups.loc[pos_groups_as_index].values

        # Calculate cluster edge id
        i = 1
        while self.pos[C.POS_GEN_EDGEDELTA_ID_CN].isnull().sum() != 0:
            # Find the mean edge
            random_edge = edge_pos_groups.sample()
            if i == 1:
                while len(pos_groups[C.POS_GEN_COORD_COLS].get_group(random_edge.index[0]).index) == 1:
                    random_edge = edge_pos_groups.sample()

            group = pos_groups[C.POS_GEN_COORD_COLS].get_group(random_edge.index[0])
            group_std = group.std().fillna(0.0) * 2  # Take the 95% of positions normal distribution
            group_mean = group.mean()
            valid_pos = self.pos.set_index(groupby).loc[edge_pos_groups.index]
            groups_near = valid_pos.loc[self.__find_posdelta(
                valid_pos[C.POS_GEN_COORD_COLS], group_mean, group_std)].drop_duplicates()
            edges_over_group = edge_pos_groups.loc[groups_near.index]
            nearest_mean = edges_over_group.iloc[
                (edges_over_group - edges_over_group.mean()).abs().sum(axis=1).argmin()]

            if edgedelta is None:
                delta = edges_over_group.std().fillna(0.0) * 2  # Take the 95% of edges normal distribution

            # Assign index at the positions of the same spread cluster
            edge_cluster = edge_pos_groups.loc[self.__find_edgedelta(edge_pos_groups, nearest_mean, delta)]
            self.pos.loc[pos_groups_as_index.isin(edge_cluster.index), C.POS_GEN_EDGEDELTA_ID_CN] = i

            # Remove groups already assigned and update the list
            edge_pos_groups = edge_pos_groups.drop(index=edge_cluster.index)
            sys.stdout.write("\r {:.3f} %".format(
                (pos_groups.ngroups - len(edge_pos_groups.index)) * 100 / pos_groups.ngroups))
            i += 1

        sys.stdout.write("\r")
        self.pos = self.pos.astype({C.POS_GEN_EDGEDELTA_ID_CN: "int64"})
        self.pos = self.pos[C.POS_GEN_COLS]

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))
        return self

    def coorddelta_heuristic(self, groupby, spreaddelta=None, edgedelta=None):
        log.d("Scooter Trajectories coorddelta heuristic")
        start = time.time()
        e_delta = edgedelta if edgedelta is not None else None
        s_delta = spreaddelta if spreaddelta is not None else None

        # Initialize column to null
        self.pos[C.POS_GEN_COORDDELTA_ID_CN] = np.nan

        # Group position for the column specified by the user
        pos_groups = self.pos.groupby(by=groupby)
        # Get the edge and the spread for each position group
        spread_pos_groups_cols = [C.POS_GEN_SPREAD_LATITUDE_CN, C.POS_GEN_SPREAD_LONGITUDE_CN]
        edge_pos_groups_cols = [C.POS_GEN_EDGE_LATITUDE_START_CN, C.POS_GEN_EDGE_LATITUDE_STOP_CN,
                                C.POS_GEN_EDGE_LONGITUDE_START_CN, C.POS_GEN_EDGE_LONGITUDE_STOP_CN]
        coord_pos_groups = self.pos[groupby + edge_pos_groups_cols + spread_pos_groups_cols].drop_duplicates()\
            .set_index(groupby)

        if type(groupby) == list:
            pos_groups_as_index = pd.MultiIndex.from_frame(self.pos[groupby])
        else:
            pos_groups_as_index = self.pos[groupby]
        i = 1
        while self.pos[C.POS_GEN_COORDDELTA_ID_CN].isnull().sum() != 0:
            # Find the mean edge
            random_edge = coord_pos_groups[edge_pos_groups_cols].sample()
            if i == 1:
                while len(pos_groups[C.POS_GEN_COORD_COLS].get_group(random_edge.index[0]).index) == 1:
                    random_edge = coord_pos_groups[edge_pos_groups_cols].sample()

            group = pos_groups[C.POS_GEN_COORD_COLS].get_group(random_edge.index[0])
            group_std = group.std().fillna(0.0) * 2  # Take the 95% of positions normal distribution
            group_mean = group.mean()
            valid_pos = self.pos.set_index(groupby).loc[coord_pos_groups.index]
            groups_near = valid_pos[edge_pos_groups_cols + spread_pos_groups_cols].loc[self.__find_posdelta(
                valid_pos[C.POS_GEN_COORD_COLS], group_mean, group_std)]
            edges_over_group = coord_pos_groups[edge_pos_groups_cols].loc[groups_near.index]
            e_nearest_mean = edges_over_group.iloc[
                (edges_over_group - edges_over_group.mean()).abs().sum(axis=1).argmin()]

            if edgedelta is None:
                e_delta = edges_over_group.std().fillna(0.0) * 2  # Take the 95% of edges normal distribution

            # Find the mean spread
            spreads_over_group = coord_pos_groups[spread_pos_groups_cols].loc[groups_near.index]
            s_nearest_mean = spreads_over_group.loc[e_nearest_mean.name].drop_duplicates().iloc[0]
            # s_nearest_mean = spreads_over_group.iloc[
            #     (spreads_over_group - spreads_over_group.mean()).abs().sum(axis=1).argmin()]

            if spreaddelta is None:
                s_delta = spreads_over_group.std().fillna(0.0) / 4  # Take the 20% of normal distribution

            # Assign index at the positions of the same coord cluster
            coord_cluster = coord_pos_groups.loc[
                self.__find_edgedelta(coord_pos_groups[edge_pos_groups_cols], e_nearest_mean, e_delta) &
                self.__find_spreaddelta(coord_pos_groups[spread_pos_groups_cols], s_nearest_mean, s_delta)]
            self.pos.loc[pos_groups_as_index.isin(coord_cluster.index), C.POS_GEN_COORDDELTA_ID_CN] = i

            # Remove groups already assigned and update the list
            coord_pos_groups = coord_pos_groups.drop(index=coord_cluster.index)
            sys.stdout.write("\r {:.3f} %".format(
                (pos_groups.ngroups - len(coord_pos_groups.index)) * 100 / pos_groups.ngroups))
            i += 1

        sys.stdout.write("\r")
        self.pos = self.pos.astype({C.POS_GEN_COORDDELTA_ID_CN: "int64"})
        self.pos = self.pos[C.POS_GEN_COLS]

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))
        return self

    def moving_behavior_feature_extraction(self, groupby, sliding_window_width=None, sliding_window_offset=None):
        log.d("Scooter Trajectories moving behavior feature extraction algorithm")
        start = time.time()

        sw_width = sliding_window_width if sliding_window_width else C.SLIDING_WINDOW_WIDTH
        sw_offset = sliding_window_offset if sliding_window_offset else C.SLIDING_WINDOW_WIDTH / 2
        sw_width, sw_offset = int(sw_width), int(sw_offset)

        # Group position in trajectories
        pos_groups = self.pos.groupby(by=groupby)
        res = pos_groups.apply(lambda x: self.__moving_attributes(x, sw_width=sw_width, sw_offset=sw_offset))
        res = res.reset_index(drop=False)
        res = res.rename(columns={res.columns[2]: C.MOVING_WINDOW_ID})
        self.moving_behavior_features = res
        self.moving_behavior_features = self.moving_behavior_features.fillna(0)

        end = time.time()
        log.d("elapsed time: {}".format(get_elapsed(start, end)))
        return self

    def heuristic_empty(self):
        """
        Heuristic columns are empty if there is a value of heuristic columns that is null.

        Returns
        -------
        Bool
            True if the heuristics columns are not fully filled otherwise false.
        """
        return self.pos[C.POS_GEN_HEURISTIC_COLS].isnull().values.any()

    def empty(self):
        return self.dataset.empty or self.pos.empty or self.rental.empty or self.merge.empty \
               or self.moving_behavior_features.empty

    def print_stats(self):
        if self.dataset.empty or self.rental.empty or self.pos.empty:
            log.e("Empty dataframe: print_stats() error")
            return self

        pd.set_option('display.max_columns', 500)
        pd.set_option('display.width', 1000)

        log.i("************************************ Scooter Trajectories - Stats *************************************")
        log.i("[DATA MAP SHAPE]: {};".format(self.dataset.shape))
        log.i("[DATA MAP COLUMN NAMES]: {};".format(list(self.dataset.columns)))
        log.i("[DATA MAP FEATURES TYPES]:\n{};".format(self.dataset.dtypes))
        log.i("[DATA MAP NULL OCCURRENCES]:\n{};".format(self.dataset.isnull().sum()))
        log.i("[DATA MAP DESCRIPTION]:\n{}\n{};".format(
            self.dataset[self.dataset.columns[:int(len(self.dataset.columns) / 2)]].describe(datetime_is_numeric=True),
            self.dataset[self.dataset.columns[int(len(self.dataset.columns) / 2):]].describe(datetime_is_numeric=True)))
        log.i("[RENTAL SHAPE]: {};".format(self.rental.shape))
        log.i("[RENTAL COLUMN NAMES]: {};".format(list(self.rental.columns)))
        log.i("[RENTAL FEATURES TYPES]:\n{};".format(self.rental.dtypes))
        log.i("[RENTAL NULL OCCURRENCES]:\n{};".format(self.rental.isnull().sum()))
        log.i("[RENTAL DESCRIPTION]:\n{};".format(self.rental.describe(datetime_is_numeric=True)))
        log.i("[POS SHAPE]: {};".format(self.pos.shape))
        log.i("[POS COLUMN NAMES]: {};".format(list(self.pos.columns)))
        log.i("[POS FEATURES TYPES]:\n{};".format(self.pos.dtypes))
        log.i("[POS NULL OCCURRENCES]:\n{};".format(self.pos.isnull().sum()))
        log.i("[POS DESCRIPTION]:\n{}\n{};".format(
            self.pos[self.pos.columns[:int(len(self.pos.columns) / 2)]].describe(datetime_is_numeric=True),
            self.pos[self.pos.columns[int(len(self.pos.columns) / 2):]].describe(datetime_is_numeric=True)))
        log.i("[MERGE SHAPE]: {};".format(self.merge.shape))
        log.i("[MERGE COLUMN NAMES]: {};".format(list(self.merge.columns)))
        log.i("[MERGE FEATURES TYPES]:\n{};".format(self.merge.dtypes))
        log.i("[MERGE NULL OCCURRENCES]:\n{};".format(self.merge.isnull().sum()))
        log.i("[MERGE DESCRIPTION]:\n{}\n{};".format(
            self.merge[self.merge.columns[:int(len(self.merge.columns) / 2)]].describe(datetime_is_numeric=True),
            self.merge[self.merge.columns[int(len(self.merge.columns) / 2):]].describe(datetime_is_numeric=True)))
        log.i("[MOVING BEHAVIOR SHAPE]: {};".format(self.moving_behavior_features.shape))
        log.i("[MOVING BEHAVIOR COLUMN NAMES]: {};".format(list(self.moving_behavior_features.columns)))
        log.i("[MOVING BEHAVIOR FEATURES TYPES]:\n{};".format(self.moving_behavior_features.dtypes))
        log.i("[MOVING BEHAVIOR NULL OCCURRENCES]:\n{};".format(self.moving_behavior_features.isnull().sum()))
        log.i("[MOVING BEHAVIOR DESCRIPTION]:\n{}\n{};".format(
            self.moving_behavior_features[self.moving_behavior_features.columns[
                :int(len(self.moving_behavior_features.columns) / 2)]].describe(datetime_is_numeric=True),
            self.moving_behavior_features[self.moving_behavior_features.columns[
                int(len(self.moving_behavior_features.columns) / 2):]].describe(datetime_is_numeric=True)))
        log.i("*******************************************************************************************************")
        return self
